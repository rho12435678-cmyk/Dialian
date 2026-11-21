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

from ddh.server import channel_for, require_guild

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_ID = "1.4.0"
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
        title=f"🚀 Dialian v{RELEASE_ID} · 의뢰·후기 이용 개선",
        description="**의뢰 진행부터 후기 확인까지, 필요한 작업을 더 짧은 동선으로 처리할 수 있습니다.**",
        colour=0xF39C12, timestamp=datetime.now(timezone.utc),
    )
    changes = (
        ("⌨️ 버튼을 찾느라 스크롤하지 않아도 돼요",
         "담당자·운영진은 `/ddh complete`로 납품 완료, `/ddh review-request`로 티켓 아래에 후기 버튼 전송. 신청자는 `/ddh review`로 바로 별점·후기를 작성하세요."),
        ("✅ 후기 등록을 티켓에서도 바로 확인",
         "후기 채널만 확인하던 흐름에서, 이제 해당 티켓에도 작성 완료·별점·후기 링크가 표시됩니다. 담당 개발자는 알림을 받고 종료 버튼이나 `/ddh close`로 대화를 읽기 전용으로 보관할 수 있습니다."),
        ("⭐ 누가 어떤 작업을 맡겼는지 한눈에",
         "후기에는 신청자·담당 개발자·GFX/VFX 등 개발 분야·1~5점 별점이 함께 표시됩니다. 실제 완료된 의뢰의 신청자만 한 번 작성할 수 있습니다."),
        ("💳 개발자가 바쁠 때도 계좌 안내 가능",
         "개발자는 `/ddh account`, 운영진은 `/ddh account developer:대상`으로 등록·수정. 티켓에서 `/ddh account-send`를 입력하면 배정된 개발자의 계좌를 안내합니다. 삭제는 `/ddh account-remove`."),
        ("📂 채널 이름으로 의뢰 분야 구분",
         "`commission-0005`처럼 분야가 보이지 않던 기본 이름을 `gfx-0005`, `vfx-0006`처럼 구분합니다. 직접 정한 채널 이름은 유지합니다."),
        ("📖 긴 설명을 핵심 단계로 정리",
         "가이드·의뢰 안내·티켓 안내를 짧게 정리했습니다. 분야·개발자 선택 → 상담·견적 → 제작·검수 → 납품·후기 순서로 이용하세요."),
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
