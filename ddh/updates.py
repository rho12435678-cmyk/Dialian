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

from ddh.blueprint import CHANNELS
from ddh.onboarding import VISIBILITY_INSTALLED
from ddh.server import UserError, find_resource, require_guild

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
GITHUB_COMMITS = "https://github.com/rho12435678-cmyk/Dialian/commit/"
SHA = re.compile(r"[0-9a-f]{40}\Z")
RECAP_KEY = "release:ddh_summary_2026_11_v2"
RECAP_MARKER = "DDH:recap:2026-11-v2"
RECAP_FIELD = "🚪 입장·인증"
UPDATE_CHANNEL = next(spec for spec in CHANNELS if spec.key == "updates")
RECAP_SECTIONS = (
    ("🗂️ 채널·역할 정리",
     "채널 이름을 한·영으로 정리하고 입장 구역을 안내 위에 배치했어요. "
     "안내에는 공지·업데이트·규칙·가이드, 운영에는 운영진 채팅과 읽기 전용 로그·보안 채널을 뒀어요. "
     "중복 질문·아이디어 채널은 운영 구역에 보존하고 기존 자료 채널은 정리 대상으로 지정했어요. "
     "관리자 역할과 외부 봇 전용 역할·명령어 채널을 추가했어요."),
    ("🛠️ Roblox 개발 커뮤니티",
     "질문·아이디어와 구인·의뢰·포트폴리오를 주제별 포럼으로 이용할 수 있어요. "
     "채팅·사진 채널에서 소통하고 라운지와 작업 음성 채널을 이용해 주세요. "
     "DDH는 개발자와 의뢰인을 연결하는 공간이에요. 의뢰 조건과 거래는 당사자가 DM에서 협의해요. "
     "기존 DDS의 봇 주문·결제와 포인트·미니게임 기능은 종료했어요."),
    ("🎫 문의·개발자 등록",
     "문의 채널에서 파트너 문의·서버 피드백·개발자 신청을 비공개로 접수해요. "
     "개발자는 GFX·UI·복장·스크립팅·VFX·빌딩·모델링·애니메이션 중 복수 분야를 선택하고 "
     "포트폴리오를 제출할 수 있어요. 승인되면 해당 분야 역할을 지급해요. "
     "파트너 승인 시 파트너 역할을 지급해요. 종료된 티켓은 채널을 정리하고 처리 기록을 남겨요."),
    ("🛡️ 로그·보안",
     "운영 기록에 실행자·대상·시각·감사 ID와 Discord가 제공한 변경 전후를 남겨요. "
     "보안 기록에서는 역할·채널 권한 변경, 삭제·제재, AutoMod 감지와 새로 발견되거나 해소된 설정 문제를 확인해요. "
     "DDH 보안 설정은 외부 서버 초대 광고·스팸·과도한 멘션을 차단하고 운영진에게 알릴 수 있어요."),
    ("🔔 공지·서버 운영",
     "Dial이 서버 설정을 관리하고 Dialian이 인증·문의·운영 기록·공지 업무를 보조해요. "
     "업데이트는 버전당 한 번 올라오며 알림 버튼에서 구독을 선택할 수 있어요. "
     "정기 이용 안내는 소유자가 켠 경우에 게시해요. 재시작 후에도 인증·문의 버튼을 사용할 수 있고, "
     "처리 중이던 티켓의 복구와 DB 백업을 지원해요."),
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


def release_embed(previous, revision, *, recap=False, entry_ready=False):
    card = discord.Embed(
        title="🚀 DDH · 전체 업데이트 안내" if recap else "🚀 DDH · 새로운 업데이트",
        description=("DDH에서 달라진 이용 방법과 운영 기능을 한곳에 정리했어요."
                     if recap else "Dialian이 DDH에서 새 배포 버전으로 연결되었습니다."),
        colour=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    if recap:
        visibility = ("인증 전에는 인증 채널만 보이고, 인증 후에는 멤버 역할로 일반 채널이 열려요."
                      if entry_ready else "입장 권한 적용이 끝나면 미인증자는 인증 채널만 볼 수 있어요.")
        card.add_field(name=RECAP_FIELD, value=(
            "새로 가입하거나 재가입한 멤버는 `인증-verify`에서 규칙을 읽고 버튼으로 인증해 주세요. "
            f"{visibility} 입장·퇴장 기록은 운영진이 확인해요."
        ), inline=False)
        for name, value in RECAP_SECTIONS:
            card.add_field(name=name, value=value, inline=False)
    else:
        card.add_field(name="✨ 이번 변경사항", value=summarize_release(previous, revision), inline=False)
    if revision:
        card.add_field(name="🔖 배포 버전", value=f"`{revision[:9]}`", inline=True)
        card.add_field(name="📋 상세 변경 내역", value=f"[GitHub에서 보기]({GITHUB_COMMITS}{revision})", inline=True)
    card.set_footer(text=RECAP_MARKER if recap else f"DDH:release:{revision}")
    return card


def matches_release(message, guild, markers):
    return message.author.id == guild.me.id and any(item.footer.text in markers for item in message.embeds)


async def publish_release(bot, guild, *, manual=False):
    """Publish the current recap or version, with a manual recovery path for deleted messages."""
    require_guild(guild, bot.settings)
    revision = bot.release_sha if bot.release_sha and SHA.fullmatch(bot.release_sha) else None
    async with bot.update_lock:
        previous = await bot.store.get(guild.id, "release:last_revision")
        recap_id = await bot.store.get(guild.id, RECAP_KEY)
        recap = not recap_id
        current = not recap and (not revision or previous == revision)
        if current and not manual:
            return False

        channel = await find_resource(guild, bot.store, "channel:updates", UPDATE_CHANNEL.name,
                                      discord.TextChannel, UPDATE_CHANNEL.aliases)
        if channel is None:
            raise UserError("`업데이트-updates` 채널을 찾지 못했습니다. 채널 이름과 Dialian의 채널 보기 권한을 확인해 주세요.")
        if guild.me is None:
            raise UserError("Discord에서 Dialian의 서버 권한을 아직 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        permissions = channel.permissions_for(guild.me)
        if not all((permissions.view_channel, permissions.read_message_history,
                    permissions.send_messages, permissions.embed_links)):
            raise UserError(f"{channel.mention}에서 Dialian에 채널 보기, 메시지 기록 보기, 메시지 보내기, 링크 첨부 권한이 필요합니다.")

        revision_marker = f"DDH:release:{revision}"
        if current:
            message_id = await bot.store.get(guild.id, "release:last_message")
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    pass
                else:
                    if matches_release(message, guild, {RECAP_MARKER, revision_marker}):
                        return False
            recap = not revision or not message_id or recap_id == message_id

        marker = RECAP_MARKER if recap else revision_marker
        # Recover a message Discord accepted before a database write completed.
        async for message in channel.history(limit=500):
            if matches_release(message, guild, {marker}):
                await remember_release(bot, channel, revision, message.id, recap)
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
        entry_ready = recap and await bot.store.get(guild.id, VISIBILITY_INSTALLED, False)
        message = await channel.send(
            content=content, embed=release_embed(previous, revision, recap=recap, entry_ready=entry_ready),
            allowed_mentions=allowed,
        )
        await remember_release(bot, channel, revision, message.id, recap)
        log.info("DDH %s announced in %s (message %s)", "recap" if recap else revision[:9], channel.id, message.id)
        return True


async def remember_release(bot, channel, revision, message_id, recap):
    guild_id = channel.guild.id
    await bot.store.set(guild_id, "release:last_message", message_id)
    await bot.store.set(guild_id, "release:last_channel", channel.id)
    await bot.store.set(guild_id, "release:last_revision", revision)
    if recap:
        await bot.store.set(guild_id, RECAP_KEY, message_id)
