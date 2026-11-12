"""Owner-installed entry controls for DDH members and external bots."""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, ROLE_ALIASES, ROLES, category_policy, overwrites
from ddh.content import publish_verification, remove_rules_verification_button
from ddh.server import UserError, create_text_board, find_resource, roles_for

INSTALLED = "onboarding:entry-controls"
ENTRY_LAYOUT = "onboarding:entry-category-arranged"
VISIBILITY_INSTALLED = "onboarding:verification-only"
CHANNEL_KEYS = {"verification", "member_log", "commands"}


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


async def suspend_personal_visibility(store, member, state):
    """Personal channel overrides can outlive a Discord membership."""
    guild = member.guild
    key = f"onboarding:personal-visibility:{member.id}"
    suspended = state.get(key, {})
    for channel in guild.channels:
        if channel.id in (state.get("channel:verification"), state.get("category:entry")):
            continue
        overwrite = channel.overwrites_for(member)
        if overwrite.view_channel is not True:
            continue
        overwrite.view_channel = False
        suspended[str(channel.id)] = [permissions.value for permissions in overwrite.pair()]
        await store.set(guild.id, key, suspended)
        await channel.set_permissions(member, overwrite=overwrite, reason="DDH 재입장: 인증 전 개별 채널 보기 제한")


async def restore_personal_visibility(store, member):
    guild = member.guild
    key = f"onboarding:personal-visibility:{member.id}"
    suspended = await store.get(guild.id, key, {})
    for channel_id, expected in suspended.items():
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            continue
        overwrite = channel.overwrites_for(member)
        if [permissions.value for permissions in overwrite.pair()] == expected:
            overwrite.view_channel = True
            await channel.set_permissions(member, overwrite=overwrite, reason="DDH 재인증: 개별 채널 보기 복구")
    if suspended:
        await store.set(guild.id, key, {})


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
            if state.get(VISIBILITY_INSTALLED):
                await restore_personal_visibility(bot.store, member)
            return
        activity = {state.get("role:member"), state.get("role:developer")}
        if any(role.id in activity for role in member.roles):
            current = await guild.fetch_member(member.id)
            if current.joined_at != member.joined_at:
                return
            restored = [role for role in current.roles if role.id in activity]
            if restored:
                await current.remove_roles(*restored, reason="DDH 재입장: 현재 가입에 대해 멤버 인증 필요")
        staff = {state.get("role:staff"), state.get("role:admin")}
        if joined and state.get(VISIBILITY_INSTALLED) and not (
            member.guild_permissions.administrator or any(role.id in staff for role in member.roles)
        ):
            await suspend_personal_visibility(bot.store, member, state)


async def restrict_visibility(guild, store, roles, entry, verification, commands):
    """Gate the whole server, retaining other permission flags and existing private boundaries."""
    everyone = guild.default_role
    staff = {guild.me.id, roles["staff"].id, roles["admin"].id}
    trusted = staff | {guild.owner_id, roles["member"].id}
    entry_ids = {entry.id, verification.id}
    state = await store.settings(guild.id)
    log_ids = {state.get(f"channel:{key}") for key in ("member_log", "audit", "security_log")}
    staff_category = state.get("category:ops")
    ticket_ids = {ticket["channel_id"] for status in ("creating", "open", "closing")
                  for ticket in await store.tickets(guild.id, status)}
    people = {}
    adjusted = []
    review = []
    channels = await guild.fetch_channels()
    for channel in channels:
        if channel.id in entry_ids:
            expected = overwrites(guild, roles, "verification")
            if channel.overwrites != expected:
                await channel.edit(overwrites=expected, reason="DDH 소유자 요청: 인증 전용 구역")
            continue

        current = channel.overwrites
        policy = channel.overwrites
        is_log = channel.id in log_ids
        is_private = (is_log or channel.id in (staff_category, state.get("channel:staff_chat"))
                      or channel.id in ticket_ids
                      or (staff_category is not None and channel.category_id == staff_category))
        was_public = not is_private and channel.permissions_for(everyone).view_channel
        default = policy.setdefault(everyone, discord.PermissionOverwrite())
        default.view_channel = False
        if was_public:
            # A new role allow would take precedence over another role's explicit deny.
            targets = (roles["staff"], roles["admin"], guild.me)
            role_deny = any(target.id != everyone.id and override.view_channel is False
                            and (isinstance(target, discord.Role)
                                 or isinstance(target, discord.Object) and target.type is discord.Role)
                            for target, override in current.items())
            if not role_deny:
                targets = (*targets, roles["member"])
            elif channel.overwrites_for(roles["member"]).view_channel is not True and channel.id != commands.id:
                review.append(channel.mention)
            for target in targets:
                override = policy.setdefault(target, discord.PermissionOverwrite())
                if override.view_channel is None:
                    override.view_channel = True
        allowed = trusted
        if is_log:
            allowed = staff
            expected = overwrites(guild, roles, "log")
            for target in (roles["staff"], roles["admin"], guild.me):
                override = policy.setdefault(target, discord.PermissionOverwrite())
                for flag in ("view_channel", "read_message_history", "send_messages"):
                    setattr(override, flag, getattr(expected[target], flag))
        elif is_private:
            allowed = trusted - {roles["member"].id}
        if channel.id == commands.id:
            allowed = allowed | {roles["external_bots"].id}
        removed = False
        for target, override in policy.items():
            if target.id in allowed or override.view_channel is not True:
                continue
            if not is_log and isinstance(target, discord.Role):
                if target.is_bot_managed():
                    continue
            elif not is_log and not (isinstance(target, discord.Object) and target.type is discord.Role):
                if target.id not in people:
                    person = guild.get_member(target.id)
                    if person is None:
                        try:
                            person = await guild.fetch_member(target.id)
                        except discord.NotFound:
                            pass
                    people[target.id] = person
                person = people[target.id]
                if person and (person.bot or any(role.id in trusted for role in person.roles)):
                    continue
            override.view_channel = None
            removed = True
        if channel.id == commands.id:
            expected = overwrites(guild, roles, "commands")
            for target in (roles["external_bots"], roles["member"], roles["staff"], roles["admin"]):
                override = policy.setdefault(target, discord.PermissionOverwrite())
                for flag in ("view_channel", "read_message_history", "send_messages", "use_application_commands"):
                    value = getattr(expected[target], flag)
                    if value is not None:
                        setattr(override, flag, value)
        policy = {target: override for target, override in policy.items() if not override.is_empty()}
        if policy != current:
            try:
                await channel.edit(overwrites=policy, reason="DDH 소유자 요청: 인증 전 채널 보기 제한")
            except discord.Forbidden:
                raise UserError(f"{channel.mention}의 권한 변경이 거부됐습니다. Dialian의 채널·권한 관리 권한을 확인한 뒤 다시 실행해 주세요.") from None
        if removed:
            adjusted.append(channel.mention)
    return adjusted, review


async def arrange_entry(guild, store, entry, info, verification, member_log):
    if await store.get(guild.id, ENTRY_LAYOUT) == entry.id:
        return
    reason = "DDH 소유자 요청: 입장 카테고리 정리"
    categories = sorted(guild.categories, key=lambda channel: (channel.position, channel.id))
    info_index = categories.index(info)
    if info_index == 0 or categories[info_index - 1].id != entry.id:
        await entry.move(before=info, reason=reason)
    if verification.category_id != entry.id or (verification.position, verification.id) > (
            member_log.position, member_log.id):
        await verification.move(category=entry, beginning=True, sync_permissions=False, reason=reason)
    if member_log.category_id != entry.id:
        last_position = max(channel.position for channel in guild.channels) + 1
        await member_log.edit(category=entry, position=last_position, sync_permissions=False, reason=reason)
    await store.set(guild.id, ENTRY_LAYOUT, entry.id)


async def install_onboarding(bot, guild):
    """Create scoped resources, then enable the entry and bot assignment rules."""
    store = bot.store
    roles = await roles_for(guild, store, keys=("staff", "admin", "member"))
    role = await external_role(guild, store)
    if role is not None:
        validate_bot_role(role, guild)

    entry_spec = next(category for category in CATEGORIES if category.key == "entry")
    info_spec = next(category for category in CATEGORIES if category.key == "info")
    info = await find_resource(guild, store, "category:info", info_spec.name,
                               discord.CategoryChannel, info_spec.aliases)
    if info is None:
        raise UserError("입장 구역을 배치할 안내 카테고리를 찾지 못했습니다.")
    entry = await find_resource(guild, store, "category:entry", entry_spec.name,
                                discord.CategoryChannel, entry_spec.aliases)
    arrange = entry is None or await store.get(guild.id, ENTRY_LAYOUT) != entry.id
    plans = []
    for spec in CHANNELS:
        if spec.key not in CHANNEL_KEYS:
            continue
        channel = await find_resource(guild, store, f"channel:{spec.key}", spec.name, discord.TextChannel, spec.aliases)
        category = None
        if channel is None and spec.category != "entry":
            parent = next(category for category in CATEGORIES if category.key == spec.category)
            category = await find_resource(guild, store, f"category:{parent.key}", parent.name,
                                           discord.CategoryChannel, parent.aliases)
            if category is None or len(category.channels) >= 50:
                raise UserError(f"{parent.name} 카테고리와 남은 채널 공간을 확인해 주세요.")
        plans.append((spec, channel, category))

    if entry is not None:
        incoming = sum(spec.category == "entry" and
                       (channel is None or (arrange and channel.category_id != entry.id))
                       for spec, channel, _ in plans)
        if len(entry.channels) + incoming > 50:
            raise UserError("입장 카테고리에 채널 공간을 확보해 주세요.")
    if entry is None:
        entry = await guild.create_category(name=entry_spec.name, overwrites=overwrites(guild, roles, category_policy(entry_spec)),
                                            reason="DDH 소유자 요청: 입장 카테고리")
    await store.set(guild.id, "category:entry", entry.id)
    if role is None:
        name, colour = ROLES["external_bots"]
        role = await guild.create_role(name=name, colour=discord.Colour(colour),
                                       permissions=discord.Permissions.none(), mentionable=False,
                                       reason="DDH 소유자 요청: 외부 봇 역할")
    roles["external_bots"] = role
    await store.set(guild.id, "role:external_bots", role.id)
    channels = {}
    for spec, channel, category in plans:
        if channel is None:
            channel = await create_text_board(guild, spec, entry if spec.category == "entry" else category, roles,
                    reason="DDH 소유자 요청: 인증·입퇴장·명령어")
        await store.set(guild.id, f"channel:{spec.key}", channel.id)
        channels[spec.key] = channel
    await arrange_entry(guild, store, entry, info, channels["verification"], channels["member_log"])
    await publish_verification(bot, guild)
    await remove_rules_verification_button(bot, guild, channels["verification"])
    adjusted, review = await restrict_visibility(guild, store, roles, entry, channels["verification"], channels["commands"])
    await store.set(guild.id, VISIBILITY_INSTALLED, True)
    await store.set(guild.id, INSTALLED, True)
    return role, [channel.mention for channel in channels.values()], adjusted, review


async def connect_existing_bots(guild, role):
    granted = 0
    blocked = 0
    async for member in guild.fetch_members(limit=None):
        try:
            granted += await grant_external_role(member, role)
        except discord.Forbidden:
            blocked += 1
    return granted, blocked
