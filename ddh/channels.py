"""REST channel enumeration tolerant of channel types newer than discord.py."""
import logging

import discord
from discord.channel import _guild_channel_factory

log = logging.getLogger(__name__)


async def fetch_supported_channels(guild):
    """Keep fresh supported channels for recovery, leaving unknown types alone.

    discord.py 2.5.2 rejects the entire response when one type is unknown.
    Use its own model factory on a fresh REST response in that case. Never
    substitute the gateway cache: recovery must find channels created before
    an interrupted database write. HTTP and malformed known-model errors
    still propagate, so callers cannot mistake a failed query for an empty
    server and create duplicate tickets/forums.
    """
    try:
        return await guild.fetch_channels()
    except discord.InvalidData:
        # This small compatibility boundary uses private APIs from the pinned
        # discord.py dependency; mixed raw-payload tests guard the adapter.
        data = await guild._state.http.get_all_guild_channels(guild.id)
        channels = []
        for item in data:
            factory, _ = _guild_channel_factory(item["type"])
            if factory is None:
                log.info("Skipping unsupported channel type %s, channel %s in guild %s",
                         item["type"], item["id"], guild.id)
                continue
            channels.append(factory(guild=guild, state=guild._state, data=item))
        return channels
