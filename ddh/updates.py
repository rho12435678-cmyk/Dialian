"""Release announcements for actual deployed Git revisions.

A release is identified once per bot process at startup, not by GitHub pushes.
A durable ledger plus a Discord-side marker prevents restart/retry spam.
"""
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import discord

from ddh.server import channel_for, require_guild, roles_for

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
GITHUB_COMMITS = "https://github.com/rho12435678-cmyk/Dialian/commit/"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(*args):
    try:
        output = subprocess.run(
            ["git", "-C", str(REPOSITORY), *args], check=True,
            capture_output=True, text=True, timeout=5,
        )
        return output.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def deployed_revision():
    """Snapshot the checked-out revision when the bot process starts."""
    revision = _git("rev-parse", "--verify", "HEAD")
    return revision if SHA.fullmatch(revision) else None


def summarize_release(previous, revision):
    """Show up to five actual deployed Git commit subjects, never invent notes."""
    if previous and SHA.fullmatch(previous):
        notes = _git("log", "--no-merges", "--format=%s", "-n", "5", f"{previous}..{revision}")
    else:
        notes = ""
    if not notes:
        notes = _git("show", "-s", "--format=%s", revision)
    lines = []
    for subject in notes.splitlines()[:5]:
        clean = discord.utils.escape_markdown(discord.utils.escape_mentions(subject.strip()))
        if clean:
            lines.append(f"• {clean[:160]}")
    return "\n".join(lines)[:1000] or "서버 안정성 및 기능 개선"


def release_embed(previous, revision, member_role=None):
    """Beautiful release card. An embed mention renders but cannot notify."""
    audience = f"🔔 **업데이트 안내 · {member_role.mention}**\\n\\n" if member_role else ""
    card = discord.Embed(
        title="🚀 DDH · 새로운 업데이트",
        description=(
            audience
            + "**Dial Dev Hub에 새로운 업데이트가 적용되었습니다.**\\n"
            + "개발자와 이용자 모두를 위한 개선 사항을 확인해 주세요."
        ),
        colour=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    card.add_field(name="✨ 이번 변경사항", value=summarize_release(previous, revision), inline=False)
    card.add_field(name="📦 적용 버전", value=f"`{revision[:9]}`", inline=True)
    card.add_field(name="📋 상세 변경 내역", value=f"[GitHub에서 보기]({GITHUB_COMMITS}{revision})", inline=True)
    card.set_footer(text=f"DDH:release:{revision}")
    return card


async def publish_release(bot, guild):
    """Automatically announce each deployed Git SHA once, including the first."""
    require_guild(guild, bot.settings)
    revision = bot.release_sha
    if not revision or not SHA.fullmatch(revision):
        return False
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False

    async with bot.update_lock:
        previous = await bot.store.get(guild.id, "release:last_revision")
        if previous == revision:
            return False
        channel = await channel_for(guild, bot.store, "updates")
        marker = f"DDH:release:{revision}"
        # A message may have been delivered before recording its revision.
        async for message in channel.history(limit=500):
            if message.author.id == guild.me.id and any(
                item.footer.text == marker for item in message.embeds
            ):
                await bot.store.set(guild.id, "release:last_revision", revision)
                return False

        member = (await roles_for(guild, bot.store, keys=("member",)))["member"]
        # Discord does NOT notify from an embed-only mention. The small first
        # message line is necessary to ping; the embed also displays the role
        # at its very top, exactly where community members expect it.
        if not channel.permissions_for(guild.me).mention_everyone:
            if not guild.me.guild_permissions.manage_channels:
                raise PermissionError(
                    "Dialian needs Mention Everyone/Roles in the updates channel"
                )
            overwrite = channel.overwrites_for(guild.me)
            overwrite.mention_everyone = True
            await channel.set_permissions(
                guild.me, overwrite=overwrite,
                reason="DDH 공식 업데이트: 봇에게만 멤버 역할 멘션 허용",
            )
        message = await channel.send(
            content=member.mention,
            embed=release_embed(previous, revision, member_role=member),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=[member],
            ),
        )
        await bot.store.set(guild.id, "release:last_revision", revision)
        await bot.store.set(guild.id, "release:last_message", message.id)
        log.info("DDH release %s announced in %s", revision[:9], channel.id)
        return True
