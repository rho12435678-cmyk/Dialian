"""Repeatable provisioning of explicitly managed DDH resources."""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, ROLE_ALIASES, ROLES, overwrites, role_permissions

REQUIRED_PERMISSIONS = (
    "manage_roles", "manage_channels", "manage_guild", "view_channel", "send_messages", "read_message_history",
    "embed_links", "attach_files", "manage_messages", "moderate_members",
    "view_audit_log", "manage_threads", "kick_members", "ban_members",
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


async def roles_for(guild, store, *, keys=ROLES):
    roles = {key: guild.get_role(await store.get(guild.id, f"role:{key}", 0)) for key in keys}
    if any(role is None for role in roles.values()):
        raise UserError("필수 역할이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
    return roles


async def channel_for(guild, store, key):
    channel = guild.get_channel(await store.get(guild.id, f"channel:{key}", 0))
    if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        raise UserError("채널이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
    return channel


def normalize_name(value):
    return " ".join(value.replace("•", "・").replace("·", "・").split()).replace(" ・ ", "・")


async def find_resource(guild, store, key, name, expected, aliases=()):
    saved = await store.get(guild.id, key)
    pool = guild.roles if expected is discord.Role else guild.channels
    item = next((obj for obj in pool if obj.id == saved), None)
    if item is not None:
        if not isinstance(item, expected):
            raise UserError(f"등록된 {name}의 종류가 다릅니다. 설정을 점검해 주세요.")
        return item
    names = {normalize_name(value) for value in (name, *aliases)}
    matches = [obj for obj in pool if normalize_name(obj.name) in names]
    if len(matches) > 1 or any(not isinstance(obj, expected) for obj in matches):
        raise UserError(f"'{name}' 이름이 중복되거나 채널 종류가 다릅니다. 중복 이름을 먼저 변경해 주세요.")
    return matches[0] if matches else None


async def find_forum_resource(guild, store, spec):
    """Select an existing forum or one legacy text board without data deletion."""
    saved = guild.get_channel(await store.get(guild.id, f"channel:{spec.key}", 0))
    names = {normalize_name(value) for value in (spec.name, *spec.aliases)}
    matches = [c for c in guild.channels if normalize_name(c.name) in names]
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
    elif spec.key == "questions":
        names = ("Studio", "스크립팅", "아트·디자인", "기타", "해결")
    elif spec.key == "ideas":
        names = ("게임", "커뮤니티", "협업", "기타")
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


async def preflight(guild, store):
    found = {}
    for key, (name, _) in ROLES.items():
        role = await find_resource(guild, store, f"role:{key}", name, discord.Role, ROLE_ALIASES.get(key, ()))
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
    if len(guild.channels) + new_channels > 500:
        raise UserError("서버 채널 수 한도에 여유가 없습니다.")
    return found


async def provision(guild, store, settings):
    require_guild(guild, settings)
    found = await preflight(guild, store)
    new_roles = [key for key in ROLES if found[f"role:{key}"] is None]
    if new_roles and not guild.me.guild_permissions.manage_roles:
        raise UserError("새 역할을 만들려면 봇에 역할 관리 권한을 부여해 주세요.")
    if any(obj is None for key, obj in found.items() if not key.startswith("role:")):
        if not guild.me.guild_permissions.manage_channels:
            raise UserError("새 채널을 만들려면 봇에 채널 관리 권한을 부여해 주세요.")
    roles = {}
    for key, (name, colour) in ROLES.items():
        role = found[f"role:{key}"]
        if role is None:
            role = await guild.create_role(
                name=name, permissions=role_permissions(key), colour=discord.Colour(colour),
                hoist=key in ("staff", "admin", "developer"), mentionable=False, reason="DDH 소유자 초기 구성")
        roles[key] = role
        await store.set(guild.id, f"role:{key}", role.id)
    categories = {}
    for spec in CATEGORIES:
        category = found[f"category:{spec.key}"]
        if category is None:
            category = await guild.create_category(
                name=spec.name, overwrites=overwrites(guild, roles, "private" if spec.private else "readonly"),
                reason="DDH 소유자 초기 구성")
        categories[spec.key] = category
        await store.set(guild.id, f"category:{spec.key}", category.id)
    news_supported = "NEWS" in guild.features
    news_pending = False
    for spec in CHANNELS:
        channel = found[f"channel:{spec.key}"]
        if channel is None:
            params = dict(name=spec.name, category=categories[spec.category], reason="DDH 소유자 초기 구성")
            if spec.forum:
                channel = await guild.create_forum(
                    **params, topic=spec.topic, slowmode_delay=30, default_thread_slowmode_delay=5,
                    default_auto_archive_duration=10080, available_tags=forum_tags(spec),
                    overwrites=forum_permissions(guild, roles, spec.policy))
            else:
                policy = overwrites(guild, roles, spec.policy, voice=spec.voice)
                if spec.voice:
                    channel = await guild.create_voice_channel(**params, overwrites=policy)
                else:
                    channel = await guild.create_text_channel(
                        **params, overwrites=policy, topic=spec.topic or "Dial Dev Hub · Roblox 개발자 커뮤니티",
                        slowmode_delay=0 if spec.policy in ("readonly", "log") else 10,
                        news=spec.news and news_supported)
        news_pending |= spec.news and channel.type != discord.ChannelType.news
        await store.set(guild.id, f"channel:{spec.key}", channel.id)
        if (spec.key == "rules" and found["channel:rules"] is None and guild.rules_channel is None
                and "COMMUNITY" in guild.features and guild.me.guild_permissions.manage_guild):
            await guild.edit(rules_channel=channel, reason="DDH 소유자 초기 구성: 규칙 채널")
    await store.set(guild.id, "news_format_pending", news_pending)
    return len(ROLES), len(CATEGORIES), len(CHANNELS)


async def ticket_category(guild, store):
    """Use the existing private Staff area: no ticket/archive category created."""
    category = guild.get_channel(await store.get(guild.id, "category:ops", 0))
    if not isinstance(category, discord.CategoryChannel):
        raise UserError("운영 카테고리가 없습니다. /ddh setup apply:True로 구성을 갱신해 주세요.")
    if len(category.channels) >= 48:
        raise UserError("운영 카테고리 공간이 부족합니다. 종료된 티켓을 먼저 정리해 주세요.")
    return category
