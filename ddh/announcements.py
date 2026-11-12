"""Continuous non-destructive DDH public announcement permission enforcement.

Announcements remain visible to everyone but posting, files and threads are
reserved for the operator, administrator and Dialian itself.
"""
import logging

import discord

from ddh.blueprint import READONLY_CHANNEL_KEYS, strict_readonly_overwrites
from ddh.server import UserError, require_guild, roles_for

log = logging.getLogger(__name__)


async def protect_announcement_channels(bot, guild):
    """Repair only DDH-managed notice channel permissions; keep all messages."""
    require_guild(guild, bot.settings)
    if not await bot.store.get(guild.id, "setup_complete", False):
        return []
    async with bot.mutation_lock:
        roles = await roles_for(guild, bot.store, keys=("staff", "admin"))
        if not guild.me.guild_permissions.manage_channels:
            raise UserError("공지 읽기 전용 권한 적용에는 Dialian의 채널 관리 권한이 필요합니다.")

        updated = []
        for key in READONLY_CHANNEL_KEYS:
            channel = guild.get_channel(
                await bot.store.get(guild.id, f"channel:{key}", 0)
            )
            if not isinstance(channel, discord.TextChannel):
                log.warning("DDH notice security: missing managed channel %s", key)
                continue
            try:
                expected = strict_readonly_overwrites(
                    guild, roles, updates=key == "updates",
                )
            except ValueError as error:
                raise UserError(str(error)) from error
            if channel.overwrites != expected:
                await channel.edit(
                    overwrites=expected,
                    reason="DDH 공지 읽기 전용: 미인증/일반 멤버 글·파일·스레드 차단",
                )
                updated.append(key)
        return updated
