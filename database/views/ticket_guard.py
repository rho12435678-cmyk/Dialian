import discord
import asyncio

from config import ARCHIVE_CATEGORY_NAME


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


def is_archive_channel(channel):
    category = getattr(channel, "category", None)
    return category is not None and category.name == ARCHIVE_CATEGORY_NAME


def get_open_ticket_channel(
    guild: discord.Guild,
    user
):
    """
    이전 티켓 조회용 함수 (필요 시 참조용으로 남겨둠)
    """
    if guild is None or user is None:
        return None

    for channel in guild.text_channels:
        if not channel.name.startswith("티켓-"):
            continue

        if is_archive_channel(channel):
            continue

        if channel.topic and str(user.id) in channel.topic:
            return channel

        if str(user.id) in channel.name:
            return channel

    return None


async def block_if_ticket_exists(interaction: discord.Interaction):
    """
    [수정] 더 빠른 방대한 작업을 위해 동일 손님이 티켓을 중복 오픈할 수 있도록 제한을 해제했습니다.
    """
    return False
