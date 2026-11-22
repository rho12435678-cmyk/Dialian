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
from ddh.server import channel_for, require_guild

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
        title=f"Dialian v{RELEASE_ID} · 의뢰와 초대 이벤트",
        description="의뢰 처리와 신규 멤버 안내가 바뀌었습니다.", colour=0xF39C12, timestamp=datetime.now(timezone.utc),
    )
    changes = (
        ("의뢰 명령어 모음", "`/의뢰도움`을 열고 배정·계좌·납품·후기 명령을 바로 선택하세요."),
        ("별점 후기", "공개 후기에 의뢰 번호, 분야, 신청자와 담당자를 표시합니다. 납품 후 `/ddh review-request`로 후기를 요청하세요."),
        ("티켓 종료", "운영진과 담당자는 종료 버튼 또는 `/ddh close`로 채널을 즉시 삭제합니다. 필요한 자료와 후기를 먼저 확인하세요."),
        ("신규 멤버 안내", "격일 오후 6시(한국 시간)에 일반 채팅에서 서버 이용 가이드를 볼 수 있습니다."),
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
    """Post this owner-authorized edition once, without a member-role ping."""
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
        message = await channel.send(embed=release_embed(marker=marker), allowed_mentions=discord.AllowedMentions.none())
        await bot.store.set(guild.id, RELEASE_SENT_KEY, message.id)
        log.info('DDH release %s sent to updates channel', RELEASE_ID)
        return True
