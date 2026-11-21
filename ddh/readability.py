"""The owner's one-time request to shorten existing copy; never repair layout."""
import logging

import discord

from ddh.commission_hub import publish_commission_hub
from ddh.content import publish_guide
from ddh.commissions import ensure_private
from ddh.server import UserError, require_guild
from ddh.tickets import ticket_channel_name

log = logging.getLogger(__name__)
COPY_KEY = 'requested-copy:1.4.0'


async def apply_requested_copy(bot, guild):
    require_guild(guild, bot.settings)
    if not await bot.store.get(guild.id, 'setup_complete', False):
        return False
    async with bot.mutation_lock:
        if await bot.store.get(guild.id, COPY_KEY, False):
            return True
        # Each completed step is durable so retries do not replace subsequent
        # owner edits to a successfully refreshed message.
        complete = True
        for name, publish in (('guide', publish_guide), ('hub', publish_commission_hub)):
            key = f'{COPY_KEY}:{name}'
            if await bot.store.get(guild.id, key, False):
                continue
            try:
                await publish(bot, guild)
            except (discord.HTTPException, UserError) as error:
                log.warning('DDH %s copy pending: %s', name, error)
                complete = False
            else:
                await bot.store.set(guild.id, key, True)
        for ticket in await bot.store.tickets(guild.id, 'open'):
            if ticket['kind'] != 'commission':
                continue
            key = COPY_KEY + f":ticket:{ticket['id']}"
            if await bot.store.get(guild.id, key, False):
                continue
            try:
                channel = await guild.fetch_channel(ticket['channel_id'])
            except discord.NotFound:
                continue
            try:
                await ensure_private(bot, guild, channel, ticket)
                # Only rename an old generated default. Custom owner names survive.
                if channel.name == f"commission-{ticket['id']:04d}":
                    await channel.edit(name=ticket_channel_name(ticket), reason='DDH 소유자 요청: 의뢰 분야 표시')
                await bot.tickets.publish_ticket(guild, channel, ticket)
            except (discord.HTTPException, UserError) as error:
                log.warning('DDH ticket %s copy pending: %s', ticket['id'], error)
                complete = False
                continue
            await bot.store.set(guild.id, key, True)
        if complete:
            await bot.store.set(guild.id, COPY_KEY, True)
        return complete
