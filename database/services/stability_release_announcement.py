"""One-time concise public announcement for September stability release.

A distinct key plus checking bot-authored Discord message history prevents
duplicate pings after restart or after an uncertain send/DB commit boundary.
"""
import asyncio
import logging

import aiosqlite
import discord

from config import CUSTOMER_ROLE_ID, PURCHASE_CHANNEL_ID
from database.database import DATABASE
from database.services.update_announcement import GUILD_ID, CHANNEL_ID

log = logging.getLogger(__name__)
_lock = asyncio.Lock()
RELEASE_KEY = "dds_stability_2026_09_28_v1"
FOOTER = "DDS Stability Release · " + RELEASE_KEY


def build_stability_embed():
    embed = discord.Embed(
        title="📢 DDS | 시스템 안정화 업데이트",
        description="더 안전하고 편리한 커미션 이용을 위해 **Dialian 시스템을 개선했습니다.**",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎨 커미션 이용",
        value=(
            "• 티켓 종료와 담당 디자이너 확인 기능을 개선했습니다.\n"
            "• 결제 정보는 **현재 담당 디자이너**만 전송할 수 있도록 강화했습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="⭐ 후기 · 포인트",
        value="후기 처리와 포인트 복구 기능을 보완해 중복 지급과 누락 위험을 줄였습니다.",
        inline=False,
    )
    embed.add_field(
        name="🛠️ 시스템 안정성",
        value="데이터 백업 검증과 오류 감지 기능을 개선했습니다.",
        inline=False,
    )
    embed.add_field(
        name="📌 이용 안내",
        value=(
            f"기존 신청 방식은 그대로입니다. <#{PURCHASE_CHANNEL_ID}>에서 이용해 주세요.\n"
            "문제가 발생하면 운영진에게 알려주세요. 감사합니다!"
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER)
    return embed


async def _record_post(message_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (RELEASE_KEY, str(message_id)),
        )
        await db.commit()


async def announce_stability_once(bot):
    """Publish once, or return False if a prior publication is confirmed."""
    async with _lock:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (RELEASE_KEY,)
            ) as cur:
                if await cur.fetchone():
                    return False

        if bot.user is None:
            raise RuntimeError("Dialian 계정이 아직 준비되지 않았습니다.")
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise RuntimeError("DDS 공지 채널 또는 서버가 일치하지 않습니다.")

        # History access is mandatory: if unavailable, do not risk an extra ping.
        async for message in channel.history(limit=1000):
            if message.author.id != bot.user.id:
                continue
            if any(
                embed.footer and embed.footer.text == FOOTER
                for embed in message.embeds
            ):
                await _record_post(message.id)
                return False

        sent = await channel.send(
            content=f"<@&{CUSTOMER_ROLE_ID}>",
            embed=build_stability_embed(),
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )
        await _record_post(sent.id)
        log.info("DDS stability notice sent once: %s", sent.id)
        return True
