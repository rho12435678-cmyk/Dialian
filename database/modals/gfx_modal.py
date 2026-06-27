import discord
import asyncio

from datetime import datetime

from config import *

from views.close_ticket import TicketCloseView

class PurchaseModal(discord.ui.Modal, title="🎨 GFX 커미션 신청서"):
    def __init__(self):
    super().__init__()
    self.selected_designer = None

    roblox_nickname = discord.ui.TextInput(
        label="GFX에 나올 로블록스 캐릭터 닉네임",
        placeholder="예: Builderman",
        required=True,
        max_length=30
    )

    gfx_type = discord.ui.TextInput(
        label="GFX 장르",
        placeholder="예: 프로필, 배너, 썸네일",
        required=True,
        max_length=50
    )

    gfx_style = discord.ui.TextInput(
        label="원하는 GFX 배경 / 스타일",
        placeholder="예: 네온도시, 전장, 다크테마",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=300
    )

    async def on_submit(self, interaction: discord.Interaction):

        guild = interaction.guild
        user = interaction.user

        ticket_channel_name = f"티켓-{user.id}"

        existing_channel = discord.utils.get(
            guild.text_channels,
            name=ticket_channel_name
        )

        if existing_channel:
            return await interaction.response.send_message(
                f"❌ 이미 생성된 티켓이 존재합니다.\n{existing_channel.mention}",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False
            ),

            user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            ),

            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True
            )
        }

        if self.selected_designer:

    dev_member = guild.get_member(self.selected_designer)

    if dev_member:

        overwrites[dev_member] = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            attach_files=True,
            embed_links=True
        )

        nickname = user.display_name.lower().replace(" ", "-")

ticket_channel_name = f"티켓-{nickname}"

        )

        form_embed = discord.Embed(
            title="📋 커미션 신청서",
            color=0x5865F2,
            timestamp=datetime.now()
        )

        form_embed.add_field(
            name="🎮 로블록스 닉네임",
            value=self.roblox_nickname.value,
            inline=False
        )

        form_embed.add_field(
            name="🖼️ GFX 장르",
            value=self.gfx_type.value,
            inline=False
        )

        form_embed.add_field(
            name="🎨 배경 / 스타일",
            value=self.gfx_style.value,
            inline=False
        )

        await ticket_channel.send(
            content=user.mention,
            embed=form_embed
        )

        guide_embed = discord.Embed(
            title="📌 추가 요구사항 작성",
            description=(
                "구체적인 요구사항들은 아래에 자유롭게 작성해주세요.\n\n"
                "• 원하는 포즈\n"
                "• 참고 이미지\n"
                "• 추가 텍스트\n"
                "• 배경 세부사항\n"
                "• 기타 요청사항"
            ),
            color=discord.Color.green()
        )

        await ticket_channel.send(
            embed=guide_embed,
            view=TicketCloseView()
        )

        log_channel = discord.utils.get(
            guild.text_channels,
            name=LOG_CHANNEL_NAME
        )

        if log_channel:
            await log_channel.send(
                f"📩 새로운 티켓 생성\n"
                f"채널: {ticket_channel.mention}\n"
                f"생성자: {user.mention}"
            )

        dev_dm_embed = discord.Embed(
            title="🔔 신규 티켓 생성",
            description=(
                f"**서버명:** `{guild.name}`\n"
                f"**생성자:** {user.mention}\n"
                f"**바로가기:** {ticket_channel.mention}"
            ),
            color=0x5865F2
        )

        if self.selected_designer:

    developer = guild.get_member(self.selected_designer)

    if developer:

        try:
            await developer.send(embed=dev_dm_embed)
        except:
            pass
