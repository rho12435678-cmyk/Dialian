"""DDS September update: one automatic, bot-authored announcement.

The release is dispatched only by the running Dialian bot (never GitHub).
Persistent bot_settings + a public-message marker protect against reposts after
restarts and crashes between the Discord send and the SQLite commit.
"""
import asyncio
import logging

import aiosqlite
import discord

from database.database import DATABASE
from config import CUSTOMER_ROLE_ID

log = logging.getLogger(__name__)
_lock = asyncio.Lock()

GUILD_ID = 1505074222161068136
CHANNEL_ID = 1506520560224571464
RELEASE_KEY = "dds_update_2026_09_24_combined_v1"
FOOTER = f"DDS Update · 2026.09.24 · {RELEASE_KEY}"


def build_update_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📢 DDS | Dialian 통합 업데이트 안내",
        description=(
            "안녕하세요. **DDS (Dial Design Studio)** 운영팀입니다.\n"
            "최근 적용된 인증·시스템 개선과 신규 티켓·포인트 업데이트를 "
            "한 번에 안내드립니다. 이용 전 주요 변경 사항을 확인해 주세요."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="🔐 01. Roblox 계정 인증",
        value=(
            "• Roblox 프로필에 **일회용 인증 코드**를 입력해 계정 소유 여부를 확인합니다.\n"
            "• 계정 생성 **30일 이상**, 현재 착용 중인 판매 가능 아이템의 "
            "조회 가격 합계 **5 Robux 이상** 조건을 검사합니다.\n"
            "• 인증 성공 시 서버 닉네임과 손님 역할이 갱신됩니다. "
            "기존 회원을 위한 **인증 정보 업데이트** 기능도 제공됩니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="📂 02. 티켓 분류 및 접근성 개선",
        value=(
            "• GFX / Roblox 복장 / 개발자 지원·파트너 문의 티켓을 "
            "**전용 카테고리**로 나누고 상단에 정리합니다.\n"
            "• 티켓명에 **분야·담당 디자이너·손님 닉네임**을 표시하며, "
            "GFX는 담당자의 **등급**도 구분합니다.\n"
            "• 담당 배정이나 등급 변경 시 티켓명이 갱신되며 "
            "신청자에게 **티켓 바로가기**도 안내합니다.\n"
            "※ 개인별 Discord Browse Channels 선택 상태는 자동 변경되지 않습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎨 03. 커미션 신청서 개선",
        value=(
            "• GFX와 복장 모두 **단품 / 2+1 / 3+1 묶음** 신청을 지원합니다.\n"
            "• 모든 복장 신청서에 **Roblox 닉네임 필수 입력란**을 추가했습니다. "
            "묶음 신청 시 작품별 닉네임도 구분해 기재할 수 있습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🪙 04. 포인트 랭킹 및 별점 후기",
        value=(
            "• 포인트 적립·차감 시 TOP 10을 즉시 갱신하고, "
            "**3분 간격**으로 재동기화합니다.\n"
            "• 신규 별점 후기의 포인트 적립 실패 시 자동 재시도하며 "
            "동일 후기의 중복 적립을 방지합니다.\n"
            "• **9월 기존 후기의 누락 포인트**는 운영팀이 지급 이력을 "
            "확인한 후 개별 복구합니다. 일괄 자동 재지급은 진행하지 않습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ 05. 관리 및 안정성 개선",
        value=(
            "• 관리자 전용 봇 재시작 기능 및 데이터베이스 정기 백업을 보완했습니다.\n"
            "• 티켓 배정·종료와 데이터 처리 오류, 보안 예외 처리도 개선했습니다.\n"
            "• 자동 번역 기능은 **운영 설정이 완료된 경우** 활성화됩니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="📌 이용 안내",
        value=(
            "기존 이용 방법은 그대로 유지됩니다. 이용 중 오류나 "
            "누락된 포인트가 발견되면 운영팀에 문의해 주세요.\n"
            "**항상 DDS를 이용해 주셔서 감사합니다!**"
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER)
    return embed


async def _save_posted(message_id: int) -> None:
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (RELEASE_KEY, str(message_id)),
        )
        await db.commit()


async def announce_once(bot) -> bool:
    """Return True only when this call posted. Do not retry blindly on uncertainty."""
    async with _lock:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (RELEASE_KEY,)
            ) as cursor:
                existing = await cursor.fetchone()
        if existing:
            log.info("DDS release notice already recorded as posted: %s", existing[0])
            return False

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise RuntimeError("DDS 업데이트 채널 및 서버 ID가 일치하지 않습니다.")
        if bot.user is None:
            raise RuntimeError("Dialian 계정 정보가 준비되지 않았습니다.")

        # If Discord accepted a previous send but the bot stopped before the
        # database commit, look for the versioned footer BEFORE trying again.
        # Fail closed if message history is unavailable, avoiding duplicates.
        async for message in channel.history(limit=250):
            if message.author.id != bot.user.id:
                continue
            if any(
                e.footer and e.footer.text == FOOTER for e in message.embeds
            ):
                await _save_posted(message.id)
                log.info("Recovered previously published DDS notice: %s", message.id)
                return False

        sent = await channel.send(
            content=f"<@&{CUSTOMER_ROLE_ID}>",
            embed=build_update_embed(),
            # Only the DDS customer role can be pinged; no @everyone or user mentions.
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                everyone=False,
                users=False,
                replied_user=False,
            ),
        )
        await _save_posted(sent.id)
        log.info("Posted one DDS release announcement to %s: %s", CHANNEL_ID, sent.id)
        return True


# DDS FAMILY trial: a separate one-time preview; never reuses the old release key.
FAMILY_KEY = "dds_family_preview_2026_09_25_v1"
FAMILY_FOOTER = f"DDS Family Preview · 2026.09.25 · {FAMILY_KEY}"
_family_lock = asyncio.Lock()


def build_family_preview_embed() -> discord.Embed:
    embed = discord.Embed(
        title="💎 DDS FAMILY | 7일 무료 체험 사전 안내",
        description=(
            "안녕하세요. **DDS (Dial Design Studio)** 운영팀입니다.\n\n"
            "기존 구매자분들을 위한 새로운 회원 서비스 **DDS FAMILY**를 "
            "**9월 26일부터 7일간 시범 운영할 예정**입니다. "
            "현재는 정식 유료 멤버십이 아닌 **무료 체험 프로그램**으로 진행합니다."
        ),
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="📅 01. 무료 체험 및 신청",
        value=(
            "• **시작 예정일:** 2026년 9월 26일\n"
            "• **대상:** DDS 기존 구매자\n"
            "• **체험 기간:** 가입 승인 후 7일\n"
            "• 신청 방법과 이용 가능 시각은 시범 운영 시작 시 별도로 안내합니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="✨ 02. 체험 예정 혜택",
        value=(
            "• **패밀리 전용 티켓**으로 전용 문의 및 체험 신청\n"
            "• 아직 출시되지 않은 **UI 등 신규 서비스의 사전 체험 신청 기회**\n"
            "• 신규 기능 체험 후 의견 및 개선 제안 참여\n"
            "※ 사전 체험은 디자이너의 작업 가능 범위와 신청 현황에 따라 "
            "제공됩니다. 무료 제작이나 우선 제작을 보장하지 않습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🛍️ 03. 기존 구매 및 단골 혜택",
        value=(
            "기존 **단품 / 2+1 / 3+1** 상품은 그대로 유지됩니다.\n"
            "기존 단골 혜택도 변경되지 않으며, 패밀리 가입은 **선택 사항**입니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🔔 04. 체험 종료 및 정식 출시",
        value=(
            "• 무료 체험 종료 후 **자동 결제되지 않습니다.**\n"
            "• 정식 패밀리권의 가격 및 세부 혜택은 시범 운영 결과와 "
            "디자이너 협의 후 별도 안내합니다.\n"
            "• 프리미엄권은 현재 **도입 보류** 상태입니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="📌 유의 사항",
        value=(
            "현재 공지는 **사전 안내**입니다. 전용 티켓과 UI 체험의 "
            "실제 이용 가능 여부 및 세부 조건은 시작 공지에서 확인해 주세요.\n"
            "**항상 DDS를 이용해 주셔서 감사합니다!**"
        ),
        inline=False,
    )
    embed.set_footer(text=FAMILY_FOOTER)
    return embed


async def announce_family_preview_once(bot) -> bool:
    """Post FAMILY preview once, recovering Discord send-before-commit crashes."""
    async with _family_lock:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (FAMILY_KEY,)
            ) as cursor:
                existing = await cursor.fetchone()
        if existing:
            log.info("DDS FAMILY preview already recorded: %s", existing[0])
            return False

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise RuntimeError("DDS FAMILY 공지 대상 채널 또는 서버가 일치하지 않습니다.")
        if bot.user is None:
            raise RuntimeError("Dialian 계정 정보가 준비되지 않았습니다.")

        # History check is mandatory; fail closed to avoid duplicate pings.
        async for message in channel.history(limit=500):
            if message.author.id != bot.user.id:
                continue
            if any(
                embed.footer and embed.footer.text == FAMILY_FOOTER
                for embed in message.embeds
            ):
                await _save_family_posted(message.id)
                return False

        sent = await channel.send(
            content=f"<@&{CUSTOMER_ROLE_ID}>",
            embed=build_family_preview_embed(),
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                users=False, everyone=False, replied_user=False,
            ),
        )
        await _save_family_posted(sent.id)
        log.info("DDS FAMILY one-time preview posted: %s", sent.id)
        return True


async def _save_family_posted(message_id: int) -> None:
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (FAMILY_KEY, str(message_id)),
        )
        await db.commit()
