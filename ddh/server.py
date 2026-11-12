"""Repeatable provisioning of explicitly managed DDH resources."""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, ROLES, everyone_permissions, overwrites, role_permissions

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
    return is_admin(member) or any(role.id == staff_id for role in member.roles)


async def roles_for(guild, store):
    roles = {key: guild.get_role(await store.get(guild.id, f"role:{key}", 0)) for key in ROLES}
    if any(role is None for role in roles.values()):
        raise UserError("필수 역할이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
    return roles


async def channel_for(guild, store, key):
    channel = guild.get_channel(await store.get(guild.id, f"channel:{key}", 0))
    if not isinstance(channel, discord.TextChannel):
        raise UserError("안내 채널이 없습니다. 관리자가 /ddh setup apply:True를 다시 실행해 주세요.")
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
        role = await find_resource(guild, store, f"role:{key}", name, discord.Role)
        if role and (role.managed or role >= guild.me.top_role):
            raise UserError(f"봇 역할을 '{name}'보다 위에 놓아 주세요. 연동 관리 역할은 사용할 수 없습니다.")
        found[f"role:{key}"] = role
    for spec in CATEGORIES:
        found[f"category:{spec.key}"] = await find_resource(
            guild, store, f"category:{spec.key}", spec.name, discord.CategoryChannel, spec.aliases)
    for spec in CHANNELS:
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
    await store.set(guild.id, "setup_complete", False)
    roles = {}
    for key, (name, colour) in ROLES.items():
        params = dict(name=name, permissions=role_permissions(key), colour=discord.Colour(colour),
                      hoist=key in ("staff", "developer"), mentionable=False, reason="DDH 서버 구성")
        role = found[f"role:{key}"]
        role = await role.edit(**params) if role else await guild.create_role(**params)
        roles[key] = role
        await store.set(guild.id, f"role:{key}", role.id)
    # Keep community roles below staff; never move the bot or unrelated roles.
    ordered = [roles[key] for key in ROLES if key != "staff"] + [roles["staff"]]
    await guild.edit_role_positions(positions={role: i + 1 for i, role in enumerate(ordered)},
                                    reason="DDH 역할 정렬")
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
    for index, spec in enumerate(CHANNELS):
        params = dict(name=spec.name, category=categories[spec.category], position=index,
                      overwrites=overwrites(guild, roles, spec.policy, voice=spec.voice), reason="DDH 채널 구성")
        if not spec.voice:
            # Discord slowmode is a baseline flood limiter, not a substitute for AutoMod.
            delay = 30 if (spec.category in ("jobs", "portfolios")) else (0 if spec.policy == "readonly" else 10)
            params.update(topic=spec.topic or "Dial Dev Hub · Roblox 개발자 커뮤니티", slowmode_delay=delay)
        channel = found[f"channel:{spec.key}"]
        if channel:
            channel = await channel.edit(**params)
        else:
            create = guild.create_voice_channel if spec.voice else guild.create_text_channel
            channel = await create(**params)
        await store.set(guild.id, f"channel:{spec.key}", channel.id)
    return len(ROLES), len(CATEGORIES), len(CHANNELS)


async def ticket_category(guild, store, roles, *, archive=False):
    """Create private overflow categories before hitting Discord's 50-child limit."""
    base = "archive" if archive else "tickets"
    name = "📁 문의 보관" if archive else "🔒 문의 진행"
    for index in range(1, 11):
        key = f"category:{base}" if index == 1 else f"category:{base}:{index}"
        label = name if index == 1 else f"{name} {index}"
        cat = await find_resource(guild, store, key, label, discord.CategoryChannel)
        if cat is None:
            cat = await guild.create_category(label, overwrites=overwrites(guild, roles, "private"),
                                              reason="DDH 비공개 문의 카테고리")
            await store.set(guild.id, key, cat.id)
        if len(cat.channels) < 48:
            return cat
    raise UserError("문의 보관 공간이 부족합니다. 운영진에게 정리를 요청해 주세요.")
