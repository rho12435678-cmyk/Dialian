import discord

from config import LOG_CHANNEL_NAME


async def send_purchase_log(guild, *, content=None, embed=None):
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if channel is None:
        print(f"[Purchase log skipped] channel not found: {LOG_CHANNEL_NAME}")
        return False
    try:
        await channel.send(content=content, embed=embed)
        return True
    except discord.HTTPException as error:
        print(f"[Purchase log failed] channel={channel.id} error={error}")
        return False
