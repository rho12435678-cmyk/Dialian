import discord
import aiosqlite

from datetime import datetime

from database.database import DATABASE
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)


class DeveloperApplyModal(discord.ui.Modal, title="개발자 지원"):

    field = discord.ui.TextInput(
        label="지원 분야",
        placeholder="예: GFX / Roblox 복장",
        required=True,
        max_length=30
    )

    experience = discord.ui.TextInput(
        label="경력",
        placeholder="예: 2년 / 없음",
        required=True,
        max_length=100
    )

    program = discord.ui.TextInput(
        label="사용 가능 프로그램",
        placeholder="예: Blender, Photoshop",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction):

        ticket_lock = await acquire_ticket_creation_lock(interaction)

        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)
        finally:
            release_ticket_creation_lock(ticket_lock)

        await interaction.response.send_message(
            "✅ 지원서가 제출되었습니다. 관리자가 확인 후 연락드리겠습니다.",
            ephemeral=True
        )

        await interaction.channel.send(embed=embed)
