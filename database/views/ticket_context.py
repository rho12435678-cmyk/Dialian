import re

import discord

TICKET_ID_PATTERN = re.compile(r"ID:\s*`?(\d{15,22})")
CHANNEL_PATTERN = re.compile(r"<#(\d{15,22})>")


async def resolve_ticket_channel(interaction, ticket_channel=None):
    if isinstance(ticket_channel, discord.TextChannel):
        return ticket_channel
    if isinstance(interaction.channel, discord.TextChannel):
        return interaction.channel
    content = getattr(interaction.message, "content", "") or ""
    match = TICKET_ID_PATTERN.search(content) or CHANNEL_PATTERN.search(content)
    if not match:
        return None
    channel_id = int(match.group(1))
    channel = interaction.client.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        channel = await interaction.client.fetch_channel(channel_id)
    except (discord.Forbidden, discord.HTTPException):
        return None
    return channel if isinstance(channel, discord.TextChannel) else None
