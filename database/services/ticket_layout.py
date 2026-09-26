"""Private DDS ticket placement and human-readable channel names.

Discord onboarding/Browse Channels preferences are client-side. This module
organizes categories and grants direct channel access; it cannot change a
member's personal channel-selection settings.
"""
import re
import secrets

import discord

CATEGORY_NAMES = {
    "gfx": "🎨 DDS｜GFX 진행 티켓",
    "uniform": "👕 DDS｜복장 진행 티켓",
    "ui": "🖥️ DDS｜UI 사전 체험 티켓",
    "support": "📩 DDS｜지원·제휴 티켓",
}


def ticket_kind(category):
    name = str(category or "").lower()
    if "ui" in name:
        return "ui"
    if "gfx" in name:
        return "gfx"
    if "복장" in name or "uniform" in name:
        return "uniform"
    return "support"


def ticket_label(category):
    name = str(category or "").lower()
    if "파트너" in name or "제휴" in name or "partner" in name:
        return "파트너"
    if "지원" in name or "apply" in name:
        return "지원"
    return {"gfx": "gfx", "uniform": "복장", "ui": "ui"}.get(ticket_kind(category), "문의")


def designer_tier(designer, category):
    if ticket_kind(category) != "gfx" or not designer:
        return ""
    role_names = [role.name for role in getattr(designer, "roles", ())]
    for tier in ("상급", "중급", "초급"):
        if any(tier in name for name in role_names):
            return tier
    return "등급미정"


def safe_name(value, limit=14):
    name = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "", str(value or "")).lower()
    return name[:limit] or "미확인"


def ticket_name(category, customer, designer=None, *, suffix=None):
    """Name: 티켓-gfx-상급-담당닉-손님닉-xxxxxx (or support equivalent)."""
    label = ticket_label(category)
    pieces = ["티켓", label]
    if ticket_kind(category) == "gfx" and designer:
        pieces.append(designer_tier(designer, category))
    pieces.append(safe_name(designer.display_name) if designer else "미배정")
    pieces.append(safe_name(customer.display_name if customer else "손님"))
    pieces.append(str(suffix or secrets.token_hex(3))[-6:])
    return "-".join(pieces)[:98]


async def get_or_create_ticket_category(guild, category):
    """Use dedicated top-level category sections; never inherit public permissions."""
    base = CATEGORY_NAMES[ticket_kind(category)]
    for index in range(1, 8):
        name = base if index == 1 else f"{base} {index}"
        existing = discord.utils.get(guild.categories, name=name)
        if existing:
            if len(existing.channels) < 48:
                return existing
            continue
        created = await guild.create_category(
            name,
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
            reason="DDS 티켓 분야별 자동 정리",
        )
        # Keep ticket categories at the top of the sidebar, after Welcome.
        # Category visibility is still subject to each user's Discord settings.
        try:
            await created.edit(position=1, reason="DDS 진행 티켓을 상단에 배치")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return created
    raise RuntimeError("진행 티켓 카테고리가 모두 가득 찼습니다. 관리자에게 알려주세요.")


async def organize_existing_ticket(channel, category, customer, designer=None):
    """Change only position/name: leave each private channel's overwrites untouched."""
    target = await get_or_create_ticket_category(channel.guild, category)
    new_name = ticket_name(category, customer, designer, suffix=str(channel.id)[-6:])
    if channel.category_id != target.id or channel.name != new_name:
        await channel.edit(
            name=new_name,
            category=target,
            sync_permissions=False,
            reason="DDS 기존 티켓 분야/담당자별 정리",
        )
    return channel
