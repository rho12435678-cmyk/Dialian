"""Discord-only DDH release notifications.

Source control is used privately for detecting a new deployment. Public Discord
messages never include the repository owner, URL, commit hash, author, or
unreviewed Git commit subjects.
"""
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import discord

from ddh import __version__
from ddh.clients import client_role
from ddh.server import UserError, channel_for, require_guild, roles_for

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_ID = __version__
RELEASE_NOTICES_KEY = f"release:{RELEASE_ID}:enabled"
RELEASE_SENT_KEY = f"release:{RELEASE_ID}:sent"
# Owner explicitly requested this release notice again. Previous paused
# releases stay paused; a new commit SHA alone never triggers another post.
RELEASE_ANNOUNCEMENTS_ALLOWED = True


def release_announcements_allowed():
    return RELEASE_ANNOUNCEMENTS_ALLOWED


def _git(*args):
    try:
        output = subprocess.run(
            ["git", "-C", str(REPOSITORY), *args],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return output.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def deployed_revision():
    """Private revision used solely to tell whether new code was deployed."""
    revision = _git("rev-parse", "--verify", "HEAD")
    return revision if SHA.fullmatch(revision) else None


def release_embed(previous=None, revision=None, member_role=None, marker=None):
    """Reviewed user-facing benefits, not guessed Git changes or speed claims."""
    card = discord.Embed(
        title=f"Dialian v{RELEASE_ID} · 초대 이벤트 집계 안내",
        description=(f"{member_role.mention}\n" if member_role else "") +
                    "초대 순위는 초대자 계정별로 집계합니다.",
        colour=0xF39C12, timestamp=datetime.now(timezone.utc),
    )
    changes = (
        ("내 초대는 어떻게 합산되나요?", "내 링크 A로 2명, 링크 B로 1명이 새로 가입하면 총 3명입니다. "
         "봇·재가입은 제외됩니다. 이벤트 순위표의 **내 초대 링크**에서 링크와 내 확정 점수를 확인하세요."),
        ("순위 반영이 늦을 때", "가입과 링크 사용 기록이 어긋나면 최대 2분간 다시 확인합니다. "
         "순위표에 최근 조회 시각과 확인이 필요한 신규 가입 건수가 표시됩니다."),
        ("이전 초대가 빠졌다면", "초대받은 분의 Discord 이름과 대략적인 가입 시각을 운영진에게 알려 주세요. "
         "운영진이 입장 기록과 초대 링크 사용 내역을 대조합니다."),
    )
    for name, value in changes:
        card.add_field(name=name, value=value, inline=False)
    card.set_footer(text=marker or f"Dial Dev Hub · Dialian v{RELEASE_ID}")
    return card


async def redact_legacy_release_links(bot, guild):
    """Replace only old bot release embeds that exposed source information.

    Previously published commit links/hashes cannot be unseen, but editing
    their Discord messages prevents continued exposure to new visitors.
    """
    require_guild(guild, bot.settings)
    if await bot.store.get(guild.id, "release:legacy_redacted", False):
        return 0
    channel = await channel_for(guild, bot.store, "updates")
    count = 0
    for message in [item async for item in channel.history(limit=500)]:
        if message.author.id != guild.me.id:
            continue
        old_revisions = [
            card.footer.text.removeprefix("DDH:release:")
            for card in message.embeds
            if isinstance(card.footer.text, str) and card.footer.text.startswith("DDH:release:")
        ]
        if not any(SHA.fullmatch(value) for value in old_revisions):
            continue

        card = discord.Embed(
            title="🚀 DDH · 이전 서버 업데이트",
            description="**Dial Dev Hub 개선 사항이 적용된 기록입니다.**",
            colour=0xF39C12,
            timestamp=getattr(message, "created_at", None),
        )
        card.add_field(
            name="✅ 적용 상태",
            value="서버 업데이트 완료 · 상세 소스 정보 비공개",
            inline=False,
        )
        card.set_footer(text="Dial Dev Hub · 이전 공식 공지")
        await message.edit(embed=card, allowed_mentions=discord.AllowedMentions.none())
        count += 1
        if bot.release_sha in old_revisions and not await bot.store.get(
            guild.id, "release:last_revision"
        ):
            await bot.store.set(guild.id, "release:last_revision", bot.release_sha)
    await bot.store.set(guild.id, "release:legacy_redacted", True)
    return count


async def publish_release(bot, guild):
    """Post this owner-authorized edition once, notifying only the verified member role."""
    require_guild(guild, bot.settings)
    if not release_announcements_allowed() or not await bot.store.get(guild.id, 'setup_complete', False):
        return False
    async with bot.update_lock:
        if not await bot.store.get(guild.id, RELEASE_NOTICES_KEY, True):
            return False
        if await bot.store.get(guild.id, RELEASE_SENT_KEY, False):
            return False
        channel = await channel_for(guild, bot.store, 'updates')
        pending_key = f'release:{RELEASE_ID}:started'
        started = await bot.store.get(guild.id, pending_key)
        if not started:
            started = datetime.now(timezone.utc).isoformat()
            await bot.store.set(guild.id, pending_key, started)
        marker = f'Dial Dev Hub · Dialian v{RELEASE_ID}'
        async for message in channel.history(limit=None, after=datetime.fromisoformat(started)):
            if message.author.id == guild.me.id and any(card.footer.text == marker for card in message.embeds):
                await bot.store.set(guild.id, RELEASE_SENT_KEY, message.id)
                return False
        member = (await roles_for(guild, bot.store, keys=('member',)))['member']
        if member.is_default() or member.managed:
            raise UserError('업데이트를 받을 멤버 역할 설정을 확인해 주세요.')
        if not (member.mentionable or channel.permissions_for(guild.me).mention_everyone):
            raise UserError('업데이트 채널에서 Dialian에 멤버 역할 멘션 권한이 필요합니다.')
        async with bot.mutation_lock:
            await client_role(bot, guild)
        message = await channel.send(content=member.mention, embed=release_embed(member_role=member, marker=marker),
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[member], replied_user=False))
        await bot.store.set(guild.id, RELEASE_SENT_KEY, message.id)
        log.info('DDH release %s sent to updates channel', RELEASE_ID)
        return True
