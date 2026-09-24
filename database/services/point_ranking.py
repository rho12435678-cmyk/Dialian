"""One shared refresh path for the DDS TOP 10 panel."""
import asyncio
import logging

import aiosqlite
import discord

from database.database import DATABASE

log = logging.getLogger(__name__)
_locks = {}


async def build_point_embed(guild):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT user_id, points FROM user_points ORDER BY points DESC, user_id ASC LIMIT 10"
        ) as cursor:
            entries = await cursor.fetchall()
    embed = discord.Embed(
        title="🏆 Dialian 포인트 랭킹 (TOP 10)",
        description="적립·차감·후기 제출 시 자동 갱신되는 포인트 순위입니다.",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    if not entries:
        embed.add_field(name="📊 순위 정보", value="아직 포인트 기록이 없습니다.", inline=False)
    else:
        medals = ["🥇 1위", "🥈 2위", "🥉 3위"]
        lines = []
        for index, (user_id, points) in enumerate(entries):
            tag = medals[index] if index < 3 else f"**{index + 1}위**"
            # Use mentions rather than expensive sequential fetch_member requests.
            lines.append(f"{tag} | <@{user_id}> — **`{points:,} P`**")
        embed.add_field(name="📊 TOP 10", value="\n".join(lines), inline=False)
    embed.set_footer(text="DDS Points · 자동 동기화")
    return embed


async def refresh_point_ranking(guild):
    if guild is None:
        return False
    # A single panel stored by configuration, not one per Discord server.
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT channel_id, message_id FROM point_ranking_panel WHERE id=1"
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return False
    channel_id, message_id = row
    if not (guild.get_channel(channel_id)):
        return False
    lock = _locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        channel = guild.get_channel(channel_id)
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=await build_point_embed(guild))
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("DDS ranking panel refresh failed: %s", exc)
            return False
