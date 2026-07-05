import discord
import asyncio


_ticket_creation_locks = {}


def get_ticket_creation_lock(user_id):
    lock = _ticket_creation_locks.get(user_id)

    if lock is None:
        lock = asyncio.Lock()
        _ticket_creation_locks[user_id] = lock

    return lock


async def acquire_ticket_creation_lock(interaction: discord.Interaction):
    lock = get_ticket_creation_lock(interaction.user.id)

    if lock.locked():
        await interaction.response.send_message(
            "티켓 생성이 처리 중입니다. 잠시만 기다려주세요.",
            ephemeral=True
        )
        return None

    await lock.acquire()
    return lock


def release_ticket_creation_lock(lock):
    if lock and lock.locked():
        lock.release()


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
