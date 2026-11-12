"""Owner-controlled migration from retired Staff role to existing 운영자.

Only DDH-managed category/channel overwrites are touched, and only the operator
entry plus the retired role. User-created overwrites and messages are kept.
"""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, ROLES, overwrites
from ddh.server import UserError, normalize_name


async def operator_role(guild):
    selected = [
        role for role in guild.roles
        if normalize_name(role.name) in ("운영자", "운영자・Operator")
    ]
    if len(selected) != 1:
        raise UserError("기존 '운영자' 역할이 정확히 하나 있어야 합니다.")
    return selected[0]


async def operator_migration(guild, store, *, apply=False):
    operator = await operator_role(guild)
    state = await store.settings(guild.id)
    roles = {
        key: operator if key == "staff" else guild.get_role(state.get(f"role:{key}", 0))
        for key in ROLES
    }
    if any(role is None for role in roles.values()):
        raise UserError("먼저 /ddh setup apply:True로 다른 역할과 채널을 연결해 주세요.")

    former = [
        role for role in guild.roles
        if role.name in ("DDH 운영진", "운영진・Staff")
        and role.id != operator.id
    ]
    scopes = []
    for spec in CATEGORIES:
        item = guild.get_channel(state.get(f"category:{spec.key}", 0))
        if isinstance(item, discord.CategoryChannel):
            scopes.append((item, "private" if spec.private else "readonly", False))
    for spec in CHANNELS:
        item = guild.get_channel(state.get(f"channel:{spec.key}", 0))
        expected = (discord.ForumChannel if spec.forum else
                    discord.VoiceChannel if spec.voice else discord.TextChannel)
        if isinstance(item, expected):
            scopes.append((item, spec.policy, spec.voice))
    for lifecycle in ("creating", "open", "closing"):
        for ticket in await store.tickets(guild.id, lifecycle):
            item = guild.get_channel(ticket["channel_id"])
            if isinstance(item, discord.TextChannel):
                scopes.append((item, "private", False))

    changes = []
    visited = set()
    for item, policy, voice in scopes:
        if item.id in visited:
            continue
        visited.add(item.id)
        active = dict(item.overwrites)
        # For existing channels, alter only the old/new operator role entries.
        desired = overwrites(guild, roles, policy, voice=voice)[operator]
        replaced = dict(active)
        replaced[operator] = desired
        for previous in former:
            replaced.pop(previous, None)
        if replaced == active:
            continue
        changes.append(item.name)
        if apply:
            if not guild.me.guild_permissions.manage_channels:
                raise UserError("봇에 채널 관리 권한이 필요합니다.")
            await item.edit(
                overwrites=replaced,
                reason="DDH 소유자 확인: 운영진 권한을 기존 운영자 역할로 이전",
            )
    if apply:
        await store.set(guild.id, "role:staff", operator.id)
    stale_roles = ", ".join(role.name for role in former) or "없음"
    action = "변경 완료" if apply else "변경 미리보기"
    return (
        f"운영자 역할: {operator.name} (ID {operator.id})\n"
        f"대상 DDH 관리 채널/카테고리 {len(changes)}개 · {action}\n"
        f"이전 운영진 역할: {stale_roles}\n"
        "기존 역할은 자동 삭제하지 않습니다. 이전 역할 보유자를 점검한 후 "
        "서버 소유자가 직접 삭제해 주세요."
    )
