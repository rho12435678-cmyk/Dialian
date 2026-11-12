"""KST reminders with a durable ledger and Discord-side crash recovery."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import discord

from ddh.content import guide
from ddh.server import channel_for, require_guild

KST = ZoneInfo("Asia/Seoul")


def due_date(now, setup_date, hour, interval):
    local = now.astimezone(KST)
    days = (local.date() - setup_date).days
    if days < 0 or days % interval or local.hour < hour:
        return None
    return local.date()


async def publish_due(bot, guild, now=None):
    require_guild(guild, bot.settings)
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False
    if not await bot.store.get(guild.id, "notices_enabled", bot.settings.notices_enabled):
        return False
    started = await bot.store.get(guild.id, "setup_at")
    if not started:
        return False
    day = due_date(now or datetime.now(timezone.utc), datetime.fromisoformat(started).astimezone(KST).date(),
                   bot.settings.notice_hour, bot.settings.notice_interval_days)
    if day is None:
        return False
    async with bot.mutation_lock:
        key = f"notice:{day.isoformat()}"
        if await bot.store.get(guild.id, key):
            return False
        channel = await channel_for(guild, bot.store, "announcements")
        marker = f"DDH:{key}:v1"
        # A send may have succeeded just before the process/DB failed.
        async for message in channel.history(limit=None, after=datetime.combine(day, time.min, KST)):
            if message.author.id == guild.me.id and any(e.footer.text == marker for e in message.embeds):
                await bot.store.set(guild.id, key, message.id)
                return False
        card = await guide(guild, bot.store)
        card.set_footer(text=marker)
        message = await channel.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
        await bot.store.set(guild.id, key, message.id)
        return True
