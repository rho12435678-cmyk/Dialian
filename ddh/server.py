"""Repeatable provisioning of explicitly managed DDH resources."""
import logging

import discord

from ddh.blueprint import (CATEGORIES, CHANNELS, OBSOLETE_CHANNEL_KEYS, OBSOLETE_CHANNEL_NAMES,
                           ROLE_ALIASES, ROLES, everyone_permissions, overwrites, role_permissions)

log = logging.getLogger(__name__)

REQUIRED_PERMISSIONS = (
    "manage_roles", "manage_channels", "manage_guild", "view_channel", "send_messages", "read_message_history",
    "embed_links", "attach_files", "manage_messages", "moderate_members",
    "mute_members", "deafen_members", "move_members", "connect", "speak",
)


class UserError(Exception):
    """An actionable message safe to show to the user."""


def require_guild(guild, settings):
    if guild is None or guild.id != settings.guild_id:
        raise UserError("이 기능은 설정된 DDH 서버에서만 사용할 수 있습니다.")


def is_admin(member):
    return isinstance(member, discord.Member) and (
        member.id == member.guild.owner_id or member.guild_permissions.administrator
    )


async def is_staff(member, store):
    if not isinstance(member, discord.Member):
        return False
    staff_id = await store.get(member.guild.id, "role:staff")
    admin_id = await store.get(member.guild.id, "role:admin")
    return is_admin(member) or any(role.id in (staff_id, admin_id) for role in member.roles)


async def roles_for(guild, store):
    roles = {key: guild.get_role(await store.get(guild.id, f"role:{key}", 0)) for key in ROLES}
    if any(role is None for role in roles.values()):
        raise UserError("필수 역할이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
    return roles


async def channel_for(guild, store, key):
    channel = guild.get_channel(await store.get(guild.id, f"channel:{key}", 0))
    if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        raise UserError("채널이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
    return channel


async def find_resource(guild, store, key, name, expected, aliases=()):
    saved = await store.get(guild.id, key)
    pool = guild.roles if expected is discord.Role else guild.channels
    item = next((obj for obj in pool if obj.id == saved), None)
    if item is not None:
        if not isinstance(item, expected):
            raise UserError(f"등록된 {name}의 종류가 다릅니다. 설정을 점검해 주세요.")
        return item
    matches = [obj for obj in pool if obj.name in (name, *aliases)]
    if len(matches) > 1 or any(not isinstance(obj, expected) for obj in matches):
        raise UserError(f"'{name}' 이름이 중복되거나 채널 종류가 다릅니다. 중복 이름을 먼저 변경해 주세요.")
    return matches[0] if matches else None


async def find_forum_resource(guild, store, spec):
    """Select an existing forum or one legacy text board without data deletion."""
    saved = guild.get_channel(await store.get(guild.id, f"channel:{spec.key}", 0))
    matches = [c for c in guild.channels if c.name in (spec.name, *spec.aliases)]
    forums = [c for c in matches if isinstance(c, discord.ForumChannel)]
    texts = [c for c in matches if isinstance(c, discord.TextChannel)]
    other = [c for c in matches if c not in (*forums, *texts)]
    if other or len(forums) > 1 or len(texts) > 1:
        raise UserError(f"'{spec.name}' 포럼 이름이 중복되거나 채널 종류가 잘못되었습니다.")
    if isinstance(saved, discord.ForumChannel):
        return saved
    if forums:
        # A prior attempt may have created the forum but failed before
        # archiving its original text channel or saving the new ID.
        return forums[0]
    if saved is not None:
        if not isinstance(saved, discord.TextChannel):
            raise UserError(f"'{spec.name}'에 저장된 채널 종류를 확인해 주세요.")
        return saved
    return texts[0] if texts else None


def forum_tags(spec):
    if spec.key == "hiring":
        names = ("유급", "협의", "팀원모집", "모집완료")
    elif spec.key == "looking":
        names = ("구직중", "외주가능", "협업가능", "마감")
    elif spec.key == "commissions":
        names = ("GFX", "UI", "복장", "VFX", "모델링", "빌딩", "애니메이션", "스크립팅", "모집완료")
    elif spec.key == "portfolio":
        names = ("GFX", "UI", "복장", "VFX", "모델링", "빌딩", "애니메이션", "스크립팅", "의뢰가능")
    else:
        names = ("포트폴리오", "의뢰가능", "작업중", "의뢰마감")
    return [discord.ForumTag(name=name) for name in names]


def forum_permissions(guild, roles, policy):
    result = overwrites(guild, roles, policy)
    # Members can discuss a developer's portfolio / hiring thread without
    # creating new portfolio/looking-for-work posts themselves.
    if policy != "member":
        result[roles["member"]] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=False, send_messages_in_threads=True,
            add_reactions=True, attach_files=True, embed_links=True,
            create_public_threads=False, create_private_threads=False,
            mention_everyone=False,
        )
    return result


async def preserve_legacy_board(guild, store, spec, category, roles, chosen_forum):
    """Keep every old text message in a readable, locked legacy channel."""
    old_id = await store.get(guild.id, f"legacy:channel:{spec.key}", 0)
    saved = guild.get_channel(old_id)
    candidates = [
        c for c in guild.channels
        if isinstance(c, discord.TextChannel)
        and c.id != chosen_forum.id and c.name in (spec.name, *spec.aliases)
    ]
    if isinstance(saved, discord.TextChannel) and saved.id != chosen_forum.id:
        candidates.append(saved)
    unique = {c.id: c for c in candidates}
    for channel in unique.values():
        old_label = (spec.name + "-기존글")[:100]
        await channel.edit(
            name=old_label, category=category,
            overwrites=overwrites(guild, roles, "readonly"),
            topic="📁 기존 일반 채널의 글을 보존한 읽기 전용 기록입니다. 새 게시물은 같은 이름의 포럼에서 작성하세요.",
            reason="DDH 포럼 이전: 기존 메시지 보존",
        )
        await store.set(guild.id, f"legacy:channel:{spec.key}", channel.id)


async def archive_obsolete(guild, store, categories, roles):
    """Hide old managed channels, preserving their messages and forum posts.

    Only channels previously tracked by DDH (or uniquely named former DDH
    channels) are moved. Existing user-created channels are never deleted.
    """
    active = {
        await store.get(guild.id, f"channel:{spec.key}", 0)
        for spec in CHANNELS
    }
    archive_cat = categories["archive"]
    moved = set()
    for key in OBSOLETE_CHANNEL_KEYS:
        saved_id = await store.get(guild.id, f"channel:{key}", 0)
        candidates = []
        if saved_id:
            saved = guild.get_channel(saved_id)
            if saved:
                candidates.append(saved)
        # Support pre-existing DDH layouts whose database mapping was lost,
        # but only adopt a single unambiguous legacy name.
        old_names = OBSOLETE_CHANNEL_NAMES.get(key, ())
        matches = [
            item for item in guild.channels
            if item.name in old_names and isinstance(
                item, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)
            )
        ]
        if len(matches) == 1:
            candidates.extend(matches)
        legacy_id = await store.get(guild.id, f"legacy:channel:{key}", 0)
        legacy = guild.get_channel(legacy_id)
        if legacy:
            candidates.append(legacy)

        for channel in candidates:
            if channel.id in active or channel.id in moved:
                continue
            moved.add(channel.id)
            name = (channel.name if channel.name.startswith("보관-")
                    else ("보관-" + channel.name)[:100])
            params = {
                "category": archive_cat, "name": name,
                "overwrites": overwrites(guild, roles, "private"),
                "reason": "DDH 미니멀 구성: 기존 데이터 삭제 없이 보관",
            }
            if isinstance(channel, discord.TextChannel):
                params["topic"] = "이전 DDH 채널의 기록입니다. 메시지는 삭제하지 않았습니다."
            await channel.edit(**params)
            log.info("Archived former DDH channel %s (%s)", key, channel.id)

    # Remove only an empty, formerly managed category. Non-empty or unfamiliar
    # categories are never deleted.
    legacy_category_id = await store.get(guild.id, "category:portfolios", 0)
    old_category = guild.get_channel(legacy_category_id)
    if isinstance(old_category, discord.CategoryChannel) and not old_category.channels:
        await old_category.delete(reason="DDH 통합 포트폴리오로 이동 후 빈 카테고리 정리")
        log.info("Deleted empty legacy portfolio category %s", legacy_category_id)



async def preflight(guild, store):
    missing = [name for name in REQUIRED_PERMISSIONS if not getattr(guild.me.guild_permissions, name)]
    if missing:
        raise UserError("봇 권한이 부족합니다: " + ", ".join(missing))
    retained = discord.Permissions.none()
    for role in guild.me.roles:
        if role != guild.default_role:
            retained.value |= role.permissions.value
    if not retained.administrator and any(not getattr(retained, name) for name in REQUIRED_PERMISSIONS):
        raise UserError("기본 역할을 제한한 뒤에도 동작하도록 봇 자체 역할에 필요한 권한을 부여해 주세요.")
    found = {}
    for key, (name, _) in ROLES.items():
        role = await find_resource(guild, store, f"role:{key}", name, discord.Role, ROLE_ALIASES.get(key, ()))
        if role and (role.managed or role >= guild.me.top_role):
            raise UserError(f"봇 역할을 '{name}'보다 위에 놓아 주세요. 연동 관리 역할은 사용할 수 없습니다.")
        found[f"role:{key}"] = role
    for spec in CATEGORIES:
        found[f"category:{spec.key}"] = await find_resource(
            guild, store, f"category:{spec.key}", spec.name, discord.CategoryChannel, spec.aliases)
    for spec in CHANNELS:
        if spec.forum:
            found[f"channel:{spec.key}"] = await find_forum_resource(guild, store, spec)
        else:
            expected = discord.VoiceChannel if spec.voice else discord.TextChannel
            found[f"channel:{spec.key}"] = await find_resource(
                guild, store, f"channel:{spec.key}", spec.name, expected, spec.aliases)
    if len(guild.roles) + sum(found[f"role:{key}"] is None for key in ROLES) > 250:
        raise UserError("서버 역할 수 한도에 여유가 없습니다.")
    new_channels = sum(value is None for key, value in found.items() if not key.startswith("role:"))
    # Text -> Forum cannot be converted in-place: one new forum per old board.
    new_channels += sum(
        isinstance(found[f"channel:{spec.key}"], discord.TextChannel)
        for spec in CHANNELS if spec.forum
    )
    if len(guild.channels) + new_channels > 500:
        raise UserError("서버 채널 수 한도에 여유가 없습니다.")
    return found


async def provision(guild, store, settings):
    require_guild(guild, settings)
    found = await preflight(guild, store)
    await store.set(guild.id, "setup_complete", False)
    roles = {}
    for key, (name, colour) in ROLES.items():
        params = dict(name=name, permissions=role_permissions(key), colour=discord.Colour(colour),
                      hoist=key in ("staff", "admin", "developer"), mentionable=False, reason="DDH 서버 구성")
        role = found[f"role:{key}"]
        role = await role.edit(**params) if role else await guild.create_role(**params)
        roles[key] = role
        await store.set(guild.id, f"role:{key}", role.id)
    # Sorting is cosmetic. Discord forbids moving a role above the bot's top
    # role; a new server often has the bot low in the role list. In that case
    # do not abort the rest of the server provisioning.
    # Low-to-high: members, notification-only, partners, specialties,
    # verified developers, staff. The bot itself remains above these roles.
    ordered_keys = (
        ["member", "update_alerts", "partner"]
        + [key for key in ROLES if key.startswith("field:")]
        + ["developer", "admin", "staff"]
    )
    ordered = [roles[key] for key in ordered_keys]
    reorder_pending = guild.me.top_role.position <= len(ordered)
    if not reorder_pending:
        try:
            await guild.edit_role_positions(
                positions={role: i + 1 for i, role in enumerate(ordered)},
                reason="DDH 역할 정렬",
            )
        except discord.HTTPException as error:
            # Discord code 50013 can occur at the bulk reorder endpoint even
            # when Manage Roles and individual role edits are permitted.
            if error.code != 50013:
                raise
            reorder_pending = True
    if reorder_pending:
        log.warning(
            "DDH role ordering skipped (Discord 50013 or insufficient role height). "
            "Move the Dialian role above all DDH roles; staff should be above "
            "ordinary developer/member roles. Other setup will proceed."
        )
    await store.set(guild.id, "role_order_pending", reorder_pending)
    await guild.default_role.edit(permissions=everyone_permissions(), reason="DDH 기본 역할 권한")
    categories = {}
    for index, spec in enumerate(CATEGORIES):
        params = dict(name=spec.name, position=index,
                      overwrites=overwrites(guild, roles, "private" if spec.private else "readonly"),
                      reason="DDH 카테고리 구성")
        cat = found[f"category:{spec.key}"]
        cat = await cat.edit(**params) if cat else await guild.create_category(**params)
        categories[spec.key] = cat
        await store.set(guild.id, f"category:{spec.key}", cat.id)
    news_supported = "NEWS" in getattr(guild, "features", ())
    await store.set(guild.id, "news_format_pending", not news_supported)
    for index, spec in enumerate(CHANNELS):
        channel = found[f"channel:{spec.key}"]
        params = dict(
            name=spec.name, category=categories[spec.category], position=index,
            reason="DDH 채널 구성",
        )
        if spec.forum:
            params.update(
                topic=spec.topic,
                slowmode_delay=30,
                default_thread_slowmode_delay=5,
                default_auto_archive_duration=10080,
                default_layout=(discord.ForumLayoutType.gallery_view
                                if spec.category == "portfolios"
                                else discord.ForumLayoutType.list_view),
                default_sort_order=discord.ForumOrderType.latest_activity,
                overwrites=forum_permissions(guild, roles, spec.policy),
            )
            params["available_tags"] = (
                channel.available_tags if isinstance(channel, discord.ForumChannel)
                and channel.available_tags else forum_tags(spec)
            )
            if isinstance(channel, discord.ForumChannel):
                channel = await channel.edit(**params)
            else:
                # Do NOT delete or transform the old text channel. Discord
                # cannot convert TextChannel directly into ForumChannel.
                forum = await guild.create_forum(**params)
                await store.set(guild.id, f"channel:{spec.key}", forum.id)
                channel = forum
            await preserve_legacy_board(
                guild, store, spec, categories[spec.category], roles, channel,
            )
        else:
            params["overwrites"] = overwrites(
                guild, roles, spec.policy, voice=spec.voice
            )
            if spec.key == "updates":
                params["overwrites"][guild.me].mention_everyone = True
            if not spec.voice:
                delay = 0 if spec.policy == "readonly" else 10
                params.update(
                    topic=spec.topic or "Dial Dev Hub · Roblox 개발자 커뮤니티",
                    slowmode_delay=delay,
                )
                if spec.news and news_supported:
                    if channel and channel.type != discord.ChannelType.news:
                        params["type"] = discord.ChannelType.news
                    elif channel is None:
                        params["news"] = True
            if channel:
                channel = await channel.edit(**params)
            else:
                create = (
                    guild.create_voice_channel if spec.voice
                    else guild.create_text_channel
                )
                channel = await create(**params)
        await store.set(guild.id, f"channel:{spec.key}", channel.id)
    # The old archive/ticket categories are intentionally not re-created.
    # Destructive cleanup must only run after an explicit owner confirmation.
    if "COMMUNITY" in getattr(guild, "features", ()):
        rules = guild.get_channel(await store.get(guild.id, "channel:rules", 0))
        if rules is not None and getattr(guild, "rules_channel", None) != rules:
            await guild.edit(rules_channel=rules, reason="DDH 규칙 채널 지정")
    return len(ROLES), len(CATEGORIES), len(CHANNELS)


async def ticket_category(guild, store, roles, *, archive=False):
    """Use the existing private Staff area: no ticket/archive category created."""
    category = guild.get_channel(await store.get(guild.id, "category:ops", 0))
    if not isinstance(category, discord.CategoryChannel):
        raise UserError("운영 카테고리가 없습니다. /ddh setup apply:True로 구성을 갱신해 주세요.")
    if len(category.channels) >= 48:
        raise UserError("운영 카테고리 공간이 부족합니다. 종료된 티켓을 먼저 정리해 주세요.")
    return category
