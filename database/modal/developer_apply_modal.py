import discord
import aiosqlite

from datetime import datetime

from database.database import DATABASE
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)


# 개발자 지원 티켓을 확인할 관리자 계정
ADMIN_IDS = [
    727462527235260427,
    1468584582113919129,
    859756809865789451,
]


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
        max_length=100
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        ticket_lock = await acquire_ticket_creation_lock(interaction)

        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)

        except discord.Forbidden:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ 봇에 채널 생성 또는 권한 설정 권한이 없습니다.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ 봇에 채널 생성 또는 권한 설정 권한이 없습니다.",
                    ephemeral=True
                )

        except Exception as error:
            print(f"[개발자 지원 티켓 생성 오류] {error}")

            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ 지원 티켓을 생성하는 중 오류가 발생했습니다.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ 지원 티켓을 생성하는 중 오류가 발생했습니다.",
                    ephemeral=True
                )

        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(
        self,
        interaction: discord.Interaction
    ):
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            return await interaction.response.send_message(
                "❌ 서버 안에서만 사용할 수 있습니다.",
                ephemeral=True
            )

        existing_channel = get_open_ticket_channel(guild, user)

        if existing_channel:
            return await interaction.response.send_message(
                f"이미 생성된 티켓이 있습니다.\n{existing_channel.mention}",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        bot_member = guild.me

        if bot_member is None and interaction.client.user:
            bot_member = guild.get_member(interaction.client.user.id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            )
        }

        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True
            )

        for admin_id in ADMIN_IDS:
            admin = guild.get_member(admin_id)

            if admin:
                overwrites[admin] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True
                )

        ticket_channel = await guild.create_text_channel(
            name=f"개발자지원-{user.id}",
            overwrites=overwrites,
            topic=str(user.id),
            reason=f"{user}의 개발자 지원 티켓"
        )

        now = datetime.now().isoformat()

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                INSERT INTO commissions(
                    ticket_channel,
                    customer_id,
                    designer_id,
                    category,
                    status,
                    progress,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_channel.id,
                    user.id,
                    None,
                    "개발자 지원",
                    "in_progress",
                    0,
                    now,
                    now
                )
            )

            await db.commit()

        embed = discord.Embed(
            title="🛠️ 개발자 지원서",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        embed.add_field(
            name="지원자",
            value=f"{user.mention}\n`{user.id}`",
            inline=False
        )

        embed.add_field(
            name="지원 분야",
            value=self.field.value,
            inline=False
        )

        embed.add_field(
            name="경력",
            value=self.experience.value,
            inline=False
        )

        embed.add_field(
            name="사용 가능 프로그램",
            value=self.program.value,
            inline=False
        )

        embed.set_footer(
            text=f"지원자: {user}"
        )

        await ticket_channel.send(
            content=(
                f"{user.mention}\n"
                "✅ 개발자 지원서가 접수되었습니다. "
                "관리자가 확인 후 이 티켓에서 안내드리겠습니다."
            ),
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                users=True
            )
        )

        await ticket_channel.send(
            "📎 포트폴리오, 작업물, 증명 자료는 "
            "이 티켓에 첨부파일로 자유롭게 올려주세요."
        )

        await interaction.followup.send(
            f"✅ 지원서가 제출되었습니다.\n{ticket_channel.mention}",
            ephemeral=True
        )
