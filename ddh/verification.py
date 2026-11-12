"""Grant the low-privilege DDH member role after Discord membership checks."""
from datetime import datetime, timedelta, timezone

import discord

from ddh.server import UserError, require_guild


async def verify_member(bot, guild, member_id, *, reviewer=None):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        await bot.tickets.ready(guild)
        try:
            member = await guild.fetch_member(member_id)
        except discord.NotFound:
            raise UserError("현재 DDH 서버에 있는 사용자만 인증할 수 있습니다.") from None
        if member.bot:
            raise UserError("봇 계정은 일반 멤버 인증을 받을 수 없습니다.")
        if member.pending:
            raise UserError(
                "Discord 기본 서버 규칙 동의가 완료되지 않았습니다. "
                "서버 상단 규칙 확인을 마친 후 다시 인증해 주세요."
            )
        if member.is_timed_out():
            raise UserError("타임아웃이 종료된 뒤 다시 멤버 인증을 진행해 주세요.")
        role = guild.get_role(await bot.store.get(guild.id, "role:member", 0))
        if role is None:
            raise UserError("멤버 역할이 누락되었습니다. 서버 운영자에게 확인을 요청해 주세요.")
        if role.managed or role.permissions.value != 0:
            raise UserError(
                "멤버 역할에 서버 전체 권한이 설정되어 있어 안전하게 인증할 수 없습니다. "
                "운영자가 멤버 역할 권한을 점검해야 합니다."
            )
        if role >= guild.me.top_role or not guild.me.guild_permissions.manage_roles:
            raise UserError(
                "Dialian이 멤버 역할을 지급할 권한이 없습니다. "
                "봇에 역할 관리 권한을 주고 역할 순서를 멤버 역할보다 위로 올려 주세요."
            )
        if role in member.roles:
            return False
        if reviewer is None and datetime.now(timezone.utc) - member.created_at < timedelta(
            days=bot.settings.verification_min_days
        ):
            raise UserError(f"Discord 계정 생성 후 {bot.settings.verification_min_days}일이 지나야 자동 인증됩니다. "
                            "관리자는 예외를 검토한 뒤 수동 인증할 수 있습니다.")
        try:
            await member.add_roles(
                role, reason="DDH 수동 인증" if reviewer else "DDH 규칙 동의 및 계정 인증",
            )
        except discord.Forbidden as error:
            raise UserError(
                "멤버 역할 지급이 거부되었습니다. "
                "운영자가 Dialian의 역할 관리 권한과 역할 순서를 확인해 주세요."
            ) from error
        except discord.HTTPException as error:
            raise UserError(
                f"Discord 역할 지급이 실패했습니다 (HTTP {error.status}). "
                "잠시 후 다시 인증하거나 운영자에게 문의해 주세요."
            ) from error
    action = f"수동 인증 · 처리자 ID `{reviewer.id}`" if reviewer else "멤버 인증"
    await bot.tickets.audit(guild, f"{action} · 멤버 ID `{member.id}`")
    return True
