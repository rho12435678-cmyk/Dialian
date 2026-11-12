"""Owner-confirmed, one-time DDH visibility migration.

The bot deliberately does NOT run this policy at startup or periodically.
After the owner applies it, subsequent manual permission edits are respected.
"""
import discord

from ddh.server import UserError, require_guild, roles_for

_ALLOWED_NAME_PARTS = ("인증", "verify", "verification")


async def find_verification_channel(guild, store, explicit=None):
    """Re-use the existing verification channel without renaming/creating it."""
    if explicit is not None:
        selected = guild.get_channel(explicit.id)
        if not isinstance(selected, discord.TextChannel):
            raise UserError("DDH 서버의 기존 인증 텍스트 채널을 지정해 주세요.")
        return selected

    saved = guild.get_channel(await store.get(guild.id, "channel:verification", 0))
    if isinstance(saved, discord.TextChannel):
        return saved

    found = [
        channel for channel in guild.channels
        if isinstance(channel, discord.TextChannel)
        and any(part in channel.name.lower() for part in _ALLOWED_NAME_PARTS)
    ]
    if len(found) != 1:
        raise UserError(
            "기존 인증 채널을 하나로 특정할 수 없습니다. "
            "/ddh visibility에서 verification_channel 옵션으로 기존 인증 채널을 지정해 주세요. "
            "새 채널은 자동 생성하지 않습니다."
        )
    return found[0]


def _copy_overwrite(overwrite):
    return discord.PermissionOverwrite.from_pair(*overwrite.pair())


async def _allowed_member_overwrite(guild, user, member_role, operator, administrator):
    """Keep verified users and owners' moderator-specific private access."""
    if user.id in (guild.owner_id, guild.me.id):
        return True
    member = guild.get_member(user.id)
    if member is None:
        try:
            member = await guild.fetch_member(user.id)
        except discord.NotFound:
            return False
    protected = {member_role.id, operator.id, administrator.id}
    return (
        any(role.id in protected for role in member.roles)
        or member.guild_permissions.administrator
    )


async def visibility_plan(bot, guild, *, verification_channel=None):
    require_guild(guild, bot.settings)
    roles = await roles_for(guild, bot.store, keys=("member", "staff", "admin"))
    member = roles["member"]
    operator = roles["staff"]
    administrator = roles["admin"]

    rules = guild.get_channel(await bot.store.get(guild.id, "channel:rules", 0))
    guide = guild.get_channel(await bot.store.get(guild.id, "channel:guide", 0))
    verification = await find_verification_channel(
        guild, bot.store, verification_channel,
    )
    if not all(isinstance(channel, discord.TextChannel) for channel in (rules, guide)):
        raise UserError("규칙·가이드 채널의 기존 연결 정보가 없습니다. 먼저 채널 ID를 확인해 주세요.")

    permitted_ids = {rules.id, guide.id, verification.id}
    if len(permitted_ids) != 3:
        raise UserError("규칙·가이드·인증이 서로 다른 3개 채널인지 확인해 주세요.")

    protected_roles = {
        guild.default_role.id, member.id, operator.id, administrator.id,
        guild.me.top_role.id,
    }
    planned = []
    # Include manual/custom channels, voice/forum and categories. Threads
    # inherit their parent permissions and do not get separate overwrites.
    for channel in guild.channels:
        overwrites = {
            target: _copy_overwrite(value)
            for target, value in channel.overwrites.items()
        }
        original = {
            target: _copy_overwrite(value)
            for target, value in overwrites.items()
        }
        visible_to_everyone = channel.permissions_for(guild.default_role).view_channel
        original_bot_visible = channel.permissions_for(guild.me).view_channel
        original_staff = {
            role.id: channel.permissions_for(role).view_channel
            for role in (operator, administrator)
        }
        public_welcome = channel.id in permitted_ids

        default = overwrites.get(guild.default_role, discord.PermissionOverwrite())
        default.view_channel = bool(public_welcome)
        overwrites[guild.default_role] = default

        # Previously public channels stay accessible to verified members.
        # Previously private/ticket channels are NOT made public to members.
        if not public_welcome and visible_to_everyone:
            member_perms = overwrites.get(member, discord.PermissionOverwrite())
            member_perms.view_channel = True
            overwrites[member] = member_perms

        # Non-member roles cannot override @everyone's deny. Verified users
        # still see public channels because their Member role grants access.
        if not public_welcome:
            for role in guild.roles:
                if role.id in protected_roles:
                    continue
                rule = overwrites.get(role, discord.PermissionOverwrite())
                rule.view_channel = False
                overwrites[role] = rule

            # Direct per-user grants can also bypass an @everyone deny.
            for target, override in list(overwrites.items()):
                if isinstance(target, discord.Role) or target.id == guild.default_role.id:
                    continue
                if override.view_channel is True and not await _allowed_member_overwrite(
                    guild, target, member, operator, administrator,
                ):
                    override.view_channel = False
                    overwrites[target] = override

        # Don't break existing bot or moderator private access.
        if original_bot_visible:
            bot_rule = overwrites.get(guild.me, discord.PermissionOverwrite())
            bot_rule.view_channel = True
            overwrites[guild.me] = bot_rule
        for role in (operator, administrator):
            if original_staff[role.id]:
                role_rule = overwrites.get(role, discord.PermissionOverwrite())
                role_rule.view_channel = True
                overwrites[role] = role_rule

        if len(overwrites) > 100:
            raise UserError(
                f"{channel.name}: Discord 채널 권한 덮어쓰기 100개 한도 초과. "
                "아무 설정도 변경하지 않았습니다. 역할 수를 검토해 주세요."
            )
        if overwrites != original:
            planned.append((channel, overwrites))
    return verification, planned


async def apply_visibility(bot, guild, *, apply=False, verification_channel=None):
    """Preview first. Only an explicit owner slash command changes permissions."""
    verification, planned = await visibility_plan(
        bot, guild, verification_channel=verification_channel,
    )
    if not apply:
        return (
            f"미인증자에게 보일 채널: 규칙 · 가이드 · {verification.name}\n"
            f"권한 변경 예정: {len(planned)}개 채널/카테고리\n"
            "다른 채널은 멤버 역할 인증 후 공개 영역만 열람합니다. "
            "운영 전용·비공개 문의는 공개하지 않습니다.\n"
            "적용은 서버 소유자가 /ddh visibility apply:True로 승인해야 합니다. "
            "적용 이후 Dialian은 수동 변경을 자동으로 되돌리지 않습니다."
        )
    if not guild.me.guild_permissions.manage_channels:
        raise UserError("Dialian 봇에 채널 관리 권한이 없어 적용할 수 없습니다.")
    changed = 0
    for channel, overwrites in planned:
        await channel.edit(
            overwrites=overwrites,
            reason="DDH 서버 소유자 요청: 미인증자는 규칙·가이드·인증만 표시",
        )
        changed += 1
    await bot.store.set(guild.id, "channel:verification", verification.id)
    return (
        f"공개 인증 진입 채널 3개 확인 · 변경 {changed}개 완료.\n"
        "이후 서버 소유자가 변경한 채널과 권한은 자동으로 되돌리지 않습니다."
    )
