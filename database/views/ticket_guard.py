import discord


def get_open_ticket_channel(
    guild: discord.Guild,
    user
):
    if guild is None or user is None:
        return None

    for channel in guild.text_channels:
        if not channel.name.startswith("티켓-"):
            continue

        if channel.topic == str(user.id):
            return channel

        if channel.name == f"티켓-{user.id}":
            return channel

    return None


async def block_if_ticket_exists(interaction: discord.Interaction):
    channel = get_open_ticket_channel(interaction.guild, interaction.user)

    if channel is None:
        return False

    await interaction.response.send_message(
        f"이미 생성된 티켓이 있습니다.\n{channel.mention}",
        ephemeral=True
    )

    return True
