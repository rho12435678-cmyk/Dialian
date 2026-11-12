"""Grant the low-privilege DDH member role after Discord membership checks."""
from datetime import datetime, timedelta, timezone

import discord

from ddh.audit_details import identity
from ddh.onboarding import restore_personal_visibility
from ddh.server import UserError, require_guild


async def verify_member(bot, guild, member_id, *, reviewer=None):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        await bot.tickets.ready(guild)
        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            raise UserError("현재 DDH 서버에 있는 사용자만 인증할 수 있습니다.") from None
        if member.bot or member.pending or member.is_timed_out():
            raise UserError("인증하려면 Discord의 서버 규칙 확인을 완료하고 타임아웃이 해제되어 있어야 합니다.")
        role = guild.get_role(await bot.store.get(guild.id, "role:member", 0))
        if role is None or role.managed or role >= guild.me.top_role or role.permissions.value != 0:
            raise UserError("멤버 역할의 권한과 순서를 확인해 주세요.")
        session = member.joined_at.isoformat() if member.joined_at else None
        session_key = f"verification:join:{member.id}"
        previous = await bot.store.get(guild.id, session_key)
        if role in member.roles and (not session or previous == session):
            await restore_personal_visibility(bot.store, member)
            return False
        if reviewer is None and datetime.now(timezone.utc) - member.created_at < timedelta(
            days=bot.settings.verification_min_days
        ):
            raise UserError(f"Discord 계정 생성 후 {bot.settings.verification_min_days}일이 지나야 자동 인증됩니다. "
                            "관리자는 예외를 검토한 뒤 수동 인증할 수 있습니다.")
        if role not in member.roles:
            await member.add_roles(role, reason="DDH 수동 인증" if reviewer else "DDH 규칙 동의 및 계정 인증")
        if session:
            await bot.store.set(guild.id, session_key, session)
        await restore_personal_visibility(bot.store, member)
    action = f"수동 인증 · 처리자 {identity(reviewer)}" if reviewer else "멤버 인증 · 본인 확인"
    await bot.tickets.audit(guild, f"**{action}**\n대상 · {identity(member)}\n인증 역할 · {identity(role)}")
    return True
