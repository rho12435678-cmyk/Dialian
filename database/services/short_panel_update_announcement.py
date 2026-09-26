"""Short, one-time Dialian announcement for the 3-button DDS panel release.

Posts only after the running bot has loaded this release. SQLite plus an exact
Discord embed footer prevent duplicate announcements across restarts/crashes.
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
RELEASE_KEY = "dds_three_menu_prices_2026_09_26_v1"
FOOTER = "DDS Panel Release · " + RELEASE_KEY


def build_short_panel_update_embed():
    """Only the new top-level categories and their key changes."""
    embed = discord.Embed(
        title="📢 DDS | 문의 패널 · 가격표 업데이트",
        description=(
            "원하는 서비스를 더 빠르게 찾을 수 있도록 "
            "**문의 패널과 가격표를 간단하게 개편했습니다.**"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎨 01 | 커미션 문의",
        value="**GFX · Roblox 복장 · FAMILY 전용 UI 사전 체험**\\n"
              "3개 분야를 한곳에서 선택할 수 있습니다.",
        inline=False,
    )
    embed.add_field(
        name="🤝 02 | 지원 & 제휴",
        value="**개발자 지원 · 파트너 문의**를 별도 메뉴로 정리했습니다.",
        inline=False,
    )
    embed.add_field(
        name="📋 03 | 가격표 개선",
        value=(
            "**일반 · 단골 20% · FAMILY 20% · FAMILY+단골 30%**\\n"
            "4종 가격표를 간결하게 통일하고, "
            "**복장 바리에이션 할인 가격**도 함께 표시합니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🔗 이용 방법",
        value=(
            f"<#{PURCHASE_CHANNEL_ID}>에서 확인하세요.\\n"
            "※ 새 3버튼 화면은 운영진이 문의 패널을 다시 게시한 후 표시됩니다."
        ),
        inline=False,
    )
    embed.set_footer(text=FOOTER)
    return embed


async def _record_post(message_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (RELEASE_KEY, str(message_id)),
        )
        await db.commit()


async def announce_short_panel_update_once(bot):
    """Return True only if Dialian actually sent this release announcement."""
    async with _lock:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (RELEASE_KEY,)
            ) as cursor:
                if await cursor.fetchone():
                    return False

        if bot.user is None:
            raise RuntimeError("Dialian 계정이 아직 준비되지 않았습니다.")
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != GUILD_ID
        ):
            raise RuntimeError("DDS 공식 업데이트 채널과 서버가 일치하지 않습니다.")

        # History is mandatory: if a crash followed sending but preceded the
        # DB write, recover the earlier message instead of pinging again.
        async for message in channel.history(limit=500):
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
            embed=build_short_panel_update_embed(),
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )
        await _record_post(sent.id)
        log.info("DDS short panel update posted once: %s", sent.id)
        return True
