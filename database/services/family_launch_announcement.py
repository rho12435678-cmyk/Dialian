"""One-time DDS FAMILY launch announcement using the actual persisted trial dates.

This module is deliberately separate from the earlier PREVIEW announcement.
Never extend or repeat the seven-day trial when publishing a notice.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord

from config import (
    BUYER_ROLE_ID,
    CUSTOMER_ROLE_ID,
    FAMILY_PROMOTION_CHANNEL_ID,
)
from database.database import DATABASE
from database.services.family import init_family_tables, start_trial_once
from database.services.update_announcement import CHANNEL_ID, GUILD_ID

log = logging.getLogger(__name__)
_lock = asyncio.Lock()
KST = timezone(timedelta(hours=9))
RELEASE_KEY = "dds_family_launch_2026_09_26_v1"
FOOTER = "DDS FAMILY Launch · " + RELEASE_KEY


def kst_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("FAMILY 체험 날짜에 UTC 시간대가 없습니다.")
    return value.astimezone(KST).strftime("%Y년 %m월 %d일 %H:%M (KST)")


def build_family_launch_embed(started: datetime, expires: datetime, now=None):
    now = now or datetime.now(KST)
    start_day = started.astimezone(KST).date()
    intro = (
        "오늘부터 **DDS FAMILY 7일 무료 체험을 시작합니다!**"
        if start_day == now.astimezone(KST).date()
        else "**DDS FAMILY 7일 무료 체험이 현재 진행 중입니다!**"
    )
    embed = discord.Embed(
        title="🎉 DDS 300명 기념 | DDS FAMILY 무료 체험 시작 안내",
        description=(
            "안녕하세요. **DDS 운영자 Dial**입니다.\n"
            + intro + "\n"
            "기존 구매자분들께 감사의 마음을 전하고자 준비한 특별 체험입니다. "
            "아래 이용 조건과 혜택을 꼭 확인해 주세요."
        ),
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="📅 ① 무료 체험 대상 및 기간",
        value=(
            f"• 대상: **체험 시작 시점에 구매자 역할(<@&{BUYER_ROLE_ID}>)을 보유한 회원**\n"
            "• 비용: **0원 / 신청 없이 자동 지급**\n"
            f"• 시작: **{kst_time(started)}**\n"
            f"• 종료: **{kst_time(expires)}**\n"
            "• 체험 종료 시 Dialian이 FAMILY 역할을 자동 회수합니다. "
            "기존 구매자·단골 역할과 이미 적립한 포인트는 유지됩니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="💎 ② FAMILY 혜택 및 할인",
        value=(
            "• **GFX / Roblox 복장 / UI 사전 체험 모두 20% 할인**\n"
            "• FAMILY + 단골 역할 동시 보유 시 **총 30% 할인** "
            "(FAMILY 20% + 단골 추가 10%, 40% 중복 할인 아님)\n"
            "• FAMILY 출석체크 **매일 15P** (일반 회원 10P)\n"
            "• 구매 후기 **1.5배 적립**: 단품 75P / 2+1 150P / 3+1 225P"
        ),
        inline=False,
    )
    embed.add_field(
        name="🖥️ ③ Roblox UI 사전 커미션 체험",
        value=(
            "FAMILY 활성 회원만 신청할 수 있는 **유료 사전 커미션**입니다.\n"
            "• 기준가: 단품 5,000원 / 2+1 10,000원 / 3+1 15,000원\n"
            "• FAMILY 20%: **4,000원 / 8,000원 / 12,000원**\n"
            "• FAMILY + 단골 30%: **3,500원 / 7,000원 / 10,500원**\n"
            "• 새 문의 패널의 UI 커미션 버튼에서 신청할 수 있습니다. "
            "**패널 교체 후** 이용 가능하며, 작업 가능 인원에 따라 접수가 제한될 수 있습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="📣 ④ FAMILY 전용 홍보 공간",
        value=(
            f"• FAMILY 역할 보유자 전용: <#{FAMILY_PROMOTION_CHANNEL_ID}>\n"
            "• 홍보 시 해당 채널의 안내 및 DDS 이용규칙을 준수해 주세요.\n"
            "• 체험 종료 또는 구독 만료 시 전용 접근 권한도 종료됩니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ ⑤ 꼭 확인해 주세요",
        value=(
            "• **7일 무료 체험은 이번 시작 시점의 기존 구매자에게만 제공**됩니다. "
            "이후 새로 구매자 역할을 얻어도 이번 체험 대상에 자동 추가되지 않습니다.\n"
            "• UI 사전 체험은 **무료 제작이 아니며**, 신청·제작 가능 여부는 담당자 안내에 따릅니다.\n"
            "• FAMILY와 단골 할인을 40%로 중복 적용하지 않습니다.\n"
            "• **무료 체험 종료 후 자동 결제·자동 유료 전환은 없습니다.**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚀 ⑥ 향후 정식 출시 계획",
        value=(
            "7일 동안 혜택 이용 현황과 개선 의견을 살펴보고, "
            "결과가 좋으면 **정식 DDS FAMILY 구독 서비스**로 출시할 예정입니다.\n"
            "정식 출시 시에는 **기존 구매자뿐 아니라 모든 일반 회원**이 신청할 수 있도록 "
            "준비합니다. 결제 확인 후 FAMILY 역할을 지급하는 방식이며, "
            "**가격·구독 기간·가입 방법·출시 여부는 추후 별도 공지**하겠습니다.\n"
            "체험 중 오류나 개선 의견은 운영진에게 알려주세요. 감사합니다! :))"
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER)
    return embed


async def _save_post(message_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (RELEASE_KEY, str(message_id)),
        )
        await db.commit()


async def announce_family_launch_once(bot) -> bool:
    """Send on startup after confirming the real trial; retry safely if unavailable.

    DB and Discord footer both protect against duplicate notices and role pings
    if the bot crashes after sending but before saving its SQLite receipt.
    """
    async with _lock:
        await init_family_tables()
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (RELEASE_KEY,)
            ) as cur:
                if await cur.fetchone():
                    return False

        guild = bot.get_guild(GUILD_ID)
        if guild is None or bot.user is None:
            raise RuntimeError("DDS 서버 또는 Dialian 계정이 준비되지 않았습니다.")

        # start_trial_once is restart-safe and cannot reset an existing start date.
        await start_trial_once(guild)
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT started_at, expires_at FROM family_trial_rollout WHERE guild_id=?",
                (GUILD_ID,),
            ) as cur:
                row = await cur.fetchone()
            async with db.execute(
                "SELECT COUNT(*) FROM family_memberships WHERE guild_id=? AND kind='trial'",
                (GUILD_ID,),
            ) as cur:
                count = (await cur.fetchone())[0]
        if row is None or count == 0:
            raise RuntimeError("FAMILY 체험 대상 및 시작 기록이 없어 시작 공지를 보류합니다.")

        started, expires = (datetime.fromisoformat(d) for d in row)
        if expires <= datetime.now(timezone.utc):
            log.warning("FAMILY trial already expired; skip stale launch notice.")
            return False

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise RuntimeError("DDS 공식 업데이트 채널이 일치하지 않습니다.")

        # Mandatory read of previous bot posts. If history cannot be read, do
        # not risk a second post or additional member notification.
        async for message in channel.history(limit=500):
            if message.author.id != bot.user.id:
                continue
            if any(e.footer and e.footer.text == FOOTER for e in message.embeds):
                await _save_post(message.id)
                return False

        sent = await channel.send(
            content=f"<@&{CUSTOMER_ROLE_ID}>",
            embed=build_family_launch_embed(started, expires),
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                users=False, everyone=False, replied_user=False,
            ),
        )
        await _save_post(sent.id)
        log.info("Posted DDS FAMILY launch announcement message=%s", sent.id)
        return True
