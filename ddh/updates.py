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

from ddh.server import UserError, channel_for, require_guild

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
GITHUB_COMMITS = "https://github.com/rho12435678-cmyk/Dialian/commit/"
SHA = re.compile(r"[0-9a-f]{40}\Z")
RECAP_KEY = "release:operations_recap"
RECAP_FIELD = "DDH 운영 기능 한눈에 보기"
RECAP = (
    "**서버 구성** · 안내 위 입장 카테고리에 인증·입퇴장 채널을 배치할 수 있습니다. "
    "가이드는 공지 채널, 질문·아이디어는 포럼으로 운영하며 자료 채널은 정리 대상입니다. "
    "명령어 채널과 외부 봇 전용 역할·권한을 지원합니다.\n"
    "**멤버 인증** · 새로 가입하거나 재가입한 멤버는 인증 채널에서 가입 건별로 인증한 뒤 멤버 역할을 받습니다. "
    "소유자가 입장 설정을 적용하면 인증 전에는 인증 채널만 열립니다. "
    "Discord 규칙 확인과 계정 조건을 검사하고, 입장과 퇴장은 운영진 기록에 남깁니다.\n"
    "**커뮤니티와 문의** · Roblox 개발자 구인·의뢰·포트폴리오 공간과 파트너·서버 피드백·개발자 신청 비공개 티켓을 운영할 수 있습니다. "
    "개발자 신청은 GFX, UI, 복장, 스크립팅, VFX, 빌딩, 모델링, 애니메이션을 지원합니다.\n"
    "**운영과 보안** · 운영 기록에는 실행자·대상·변경 전후를, 보안 채널에는 권한·삭제·제재 등 주요 감사 이벤트와 설정 점검을 표시합니다. "
    "AutoMod와 문의 복구·데이터 백업을 지원합니다.\n"
    "**운영 권한** · Dial이 서버를 운영하며 Dialian은 인증·기록·알림을 보조합니다. "
    "채널·역할·보안 구성 적용은 소유자 명령으로 진행합니다."
)


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


def release_embed(previous, revision, *, recap=False):
    card = discord.Embed(
        title="🚀 DDH · 운영 시스템 업데이트" if recap else "🚀 DDH · 새로운 업데이트",
        description="Dialian이 DDH에서 새 배포 버전으로 연결되었습니다.",
        colour=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    if recap:
        card.add_field(name=RECAP_FIELD, value=RECAP, inline=False)
    card.add_field(name="✨ 이번 변경사항", value=summarize_release(previous, revision), inline=False)
    card.add_field(name="🔖 배포 버전", value=f"`{revision[:9]}`", inline=True)
    card.add_field(name="📋 상세 변경 내역", value=f"[GitHub에서 보기]({GITHUB_COMMITS}{revision})", inline=True)
    card.set_footer(text=f"DDH:release:{revision}")
    return card


async def publish_release(bot, guild):
    """Announce a deployed revision once and include the DDH recap on first delivery."""
    require_guild(guild, bot.settings)
    revision = bot.release_sha
    if not revision or not SHA.fullmatch(revision):
        return False
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False

    async with bot.update_lock:
        previous = await bot.store.get(guild.id, "release:last_revision")
        recap = not await bot.store.get(guild.id, RECAP_KEY, False)
        if previous == revision and not recap:
            return False

        channel = await channel_for(guild, bot.store, "updates")
        if not isinstance(channel, discord.TextChannel):
            raise UserError("업데이트 공지는 등록된 텍스트 또는 공지 채널에서만 게시할 수 있습니다.")
        marker = f"DDH:release:{revision}"
        # A message may have been delivered before recording its revision.
        async for message in channel.history(limit=500):
            if message.author.id != guild.me.id:
                continue
            card = next((item for item in message.embeds if item.footer.text == marker), None)
            if card and (not recap or any(field.name == RECAP_FIELD for field in card.fields)):
                await remember_release(bot, guild, revision, message.id, recap)
                return False

        mode = bot.settings.update_ping_mode
        role = guild.get_role(await bot.store.get(guild.id, "role:update_alerts", 0)) if mode == "role" else None
        if mode == "everyone":
            content = "@everyone"
            allowed = discord.AllowedMentions(everyone=True, users=False, roles=False)
        elif role is not None and role.id != guild.id:
            content = role.mention
            allowed = discord.AllowedMentions(everyone=False, users=False, roles=[role])
        else:
            content = None
            allowed = discord.AllowedMentions.none()
        message = await channel.send(
            content=content, embed=release_embed(previous, revision, recap=recap),
            allowed_mentions=allowed,
        )
        await remember_release(bot, guild, revision, message.id, recap)
        log.info("DDH release %s announced in %s", revision[:9], channel.id)
        return True


async def remember_release(bot, guild, revision, message_id, recap):
    await bot.store.set(guild.id, "release:last_message", message_id)
    if recap:
        await bot.store.set(guild.id, RECAP_KEY, True)
    await bot.store.set(guild.id, "release:last_revision", revision)
