"""Owner-installed entry controls for DDH members and external bots."""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, ROLE_ALIASES, ROLES, access
from ddh.content import publish_verification
from ddh.operations import private_channel
from ddh.server import UserError, create_text_board, find_forum_resource, find_resource, roles_for

INSTALLED = "onboarding:entry-controls"
CHANNEL_KEYS = {"verification", "member_log", "commands"}
ACTIVITY_FLAGS = (
    "send_messages", "send_messages_in_threads", "create_public_threads",
    "create_private_threads", "connect", "speak", "use_application_commands",
)


async def external_role(guild, store):
    name, _ = ROLES["external_bots"]
    return await find_resource(guild, store, "role:external_bots", name, discord.Role,
                               ROLE_ALIASES.get("external_bots", ()))


def validate_bot_role(role, guild):
    if role.managed or role.permissions.value or role >= guild.me.top_role:
        raise UserError("외부봇 역할은 서버 전체 권한을 비우고 Dialian 역할 아래에 두어 주세요.")


async def grant_external_role(member, role):
    guild = member.guild
    if member.bot and member.id != guild.me.id and role not in member.roles:
        await member.add_roles(role, reason="DDH 외부 봇 명령어 채널 연결")
        return True
    return False


async def enforce_entry(bot, member, *, joined=False):
    """Handle new memberships after the owner has installed the controls."""
    guild = member.guild
    if guild.id != bot.settings.guild_id or not await bot.store.get(guild.id, INSTALLED):
        return
    async with bot.mutation_lock:
        if member.bot:
            if not joined:
                return
            role = guild.get_role(await bot.store.get(guild.id, "role:external_bots", 0))
            if role and member.id != guild.me.id:
                validate_bot_role(role, guild)
                await grant_external_role(member, role)
            return
        if member.id == guild.owner_id:
            return
        if member.joined_at is None:
            return
        session = member.joined_at.isoformat()
        observed_key = f"onboarding:joined:{member.id}"
        if joined:
            await bot.store.set(guild.id, observed_key, session)
        elif await bot.store.get(guild.id, observed_key) != session:
            return
        state = await bot.store.settings(guild.id)
        if state.get(f"verification:join:{member.id}") == session:
            return
        activity = {state.get("role:member"), state.get("role:developer")}
        if not any(role.id in activity for role in member.roles):
            return
        current = await guild.fetch_member(member.id)
        if current.joined_at != member.joined_at:
            return
        restored = [role for role in current.roles if role.id in activity]
        if restored:
            await current.remove_roles(*restored, reason="DDH 재입장: 현재 가입에 대해 멤버 인증 필요")


async def restrict_public_activity(guild, store, roles):
    """Update activity permissions without replacing the other channel settings."""
    state = await store.settings(guild.id)
    exceptions = []
    trusted = {guild.me.id, state.get("role:member"), state.get("role:developer"),
               state.get("role:staff"), state.get("role:admin"), state.get("role:external_bots")}
    for spec in CHANNELS:
        if spec.policy not in ("member", "developer", "commands"):
            continue
        channel = guild.get_channel(state.get(f"channel:{spec.key}", 0))
        if channel is None:
            if spec.forum:
                channel = await find_forum_resource(guild, store, spec)
            else:
                expected = discord.VoiceChannel if spec.voice else discord.TextChannel
                channel = await find_resource(
                    guild, store, f"channel:{spec.key}", spec.name, expected, spec.aliases)
            if channel is None:
                continue
            await store.set(guild.id, f"channel:{spec.key}", channel.id)
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel)):
            exceptions.append(channel.mention)
            continue
        everyone = channel.overwrites_for(guild.default_role)
        if any(getattr(everyone, flag) is not False for flag in ACTIVITY_FLAGS):
            for flag in ACTIVITY_FLAGS:
                setattr(everyone, flag, False)
            await channel.set_permissions(guild.default_role, overwrite=everyone,
                                          reason="DDH 소유자 요청: 인증 전 활동 제한")
        if spec.policy == "commands":
            writers = [roles[key] for key in ("member", "staff", "admin")]
        elif spec.policy == "developer":
            writers = [guild.get_role(state.get("role:developer", 0))]
        else:
            writers = [roles["member"]]
        for writer in writers:
            if writer is None:
                continue
            current = channel.overwrites_for(writer)
            grants = {"view_channel": True, "send_messages": True}
            if spec.voice:
                grants.update(connect=True, speak=True)
            if spec.forum:
                grants["send_messages_in_threads"] = True
            if spec.policy == "commands":
                grants["use_application_commands"] = True
            if any(getattr(current, flag) != value for flag, value in grants.items()):
                current.update(**grants)
                await channel.set_permissions(writer, overwrite=current, reason="DDH 인증 멤버 활동 허용")
        if spec.policy == "commands":
            bot_role = roles["external_bots"]
            policy = channel.overwrites_for(bot_role)
            if policy != access():
                await channel.set_permissions(bot_role, overwrite=access(),
                                              reason="DDH 외부 봇 명령어 채널 응답 권한")
        if any(target.id not in trusted and target.id != guild.default_role.id
               and any(getattr(overwrite, flag) is True for flag in ACTIVITY_FLAGS)
               for target, overwrite in channel.overwrites.items()):
            exceptions.append(channel.mention)
    return exceptions


async def install_onboarding(bot, guild):
    """Create scoped resources, then enable the entry and bot assignment rules."""
    store = bot.store
    roles = await roles_for(guild, store, keys=("staff", "admin", "member"))
    role = await external_role(guild, store)
    if role is not None:
        validate_bot_role(role, guild)

    plans = []
    for spec in CHANNELS:
        if spec.key not in CHANNEL_KEYS:
            continue
        channel = await find_resource(guild, store, f"channel:{spec.key}", spec.name, discord.TextChannel, spec.aliases)
        category = None
        if channel is None:
            parent = next(category for category in CATEGORIES if category.key == spec.category)
            category = await find_resource(guild, store, f"category:{parent.key}", parent.name,
                                           discord.CategoryChannel, parent.aliases)
            if category is None or len(category.channels) >= 50:
                raise UserError(f"{parent.name} 카테고리와 남은 채널 공간을 확인해 주세요.")
        if spec.key == "member_log" and channel is not None and not private_channel(
            channel, guild, {roles["staff"].id, roles["admin"].id, guild.me.id}
        ):
            raise UserError("입퇴장 채널을 Staff 전용으로 제한한 뒤 다시 실행해 주세요.")
        plans.append((spec, channel, category))

    if role is None:
        name, colour = ROLES["external_bots"]
        role = await guild.create_role(name=name, colour=discord.Colour(colour),
                                       permissions=discord.Permissions.none(), mentionable=False,
                                       reason="DDH 소유자 요청: 외부 봇 역할")
    roles["external_bots"] = role
    await store.set(guild.id, "role:external_bots", role.id)
    mentions = []
    for spec, channel, category in plans:
        if channel is None:
            channel = await create_text_board(guild, spec, category, roles, reason="DDH 소유자 요청: 인증·입퇴장·명령어")
        await store.set(guild.id, f"channel:{spec.key}", channel.id)
        mentions.append(channel.mention)
    await publish_verification(bot, guild)
    exceptions = await restrict_public_activity(guild, store, roles)
    await store.set(guild.id, INSTALLED, True)
    return role, mentions, exceptions


async def connect_existing_bots(guild, role):
    granted = 0
    blocked = 0
    async for member in guild.fetch_members(limit=None):
        try:
            granted += await grant_external_role(member, role)
        except discord.Forbidden:
            blocked += 1
    return granted, blocked
