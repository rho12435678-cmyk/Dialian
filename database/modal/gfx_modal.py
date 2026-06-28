import discord

from datetime import datetime
from config import *
from database.views.close_ticket import TicketCloseView
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView


class PurchaseModal(discord.ui.Modal):

    MODAL_TITLE = "🎨 GFX 커미션 신청서"

    FORM_TITLE = "📋 GFX 커미션 신청서"
    FIELD1 = "🎮 Roblox 닉네임"
    FIELD2 = "🖼 GFX 종류"
    FIELD3 = "🎨 원하는 스타일"

    def __init__(self):
        super().__init__(title=self.MODAL_TITLE)

        self.selected_designer = None

        self.roblox_nickname = discord.ui.TextInput(
            label=self.FIELD1,
            placeholder="입력해주세요.",
            required=True,
            max_length=30
        )

        self.gfx_type = discord.ui.TextInput(
            label=self.FIELD2,
            placeholder="입력해주세요.",
            required=True,
            max_length=50
        )

        self.gfx_style = discord.ui.TextInput(
            label=self.FIELD3,
            placeholder="입력해주세요.",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=300
        )

        self.add_item(self.roblox_nickname)
        self.add_item(self.gfx_type)
        self.add_item(self.gfx_style)

    async def on_submit(self, interaction: discord.Interaction):

        guild = interaction.guild
        user = interaction.user

        designer_name = "미지정"

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)
            designer_name = developer.mention if developer else "미지정"

        nickname = user.display_name.lower().replace(" ", "-")
        ticket_channel_name = f"티켓-{nickname}"

        if discord.utils.get(guild.text_channels, name=ticket_channel_name):
            return await interaction.response.send_message(
                "이미 생성된 티켓이 있습니다.",
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
                attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True
            )
        }

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                overwrites[developer] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=True
                )

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=self.FORM_TITLE,
            color=0x5865F2,
            timestamp=datetime.now()
        )

        embed.add_field(
            name="👨‍💻 담당 디자이너",
            value=designer_name,
            inline=False
        )

        embed.add_field(
            name=self.FIELD1,
            value=self.roblox_nickname.value,
            inline=False
        )

        embed.add_field(
            name=self.FIELD2,
            value=self.gfx_type.value,
            inline=False
        )

        embed.add_field(
            name=self.FIELD3,
            value=self.gfx_style.value,
            inline=False
        )


        await ticket_channel.send(
            content=user.mention,
            embed=embed
        )

        progress_message = await ticket_channel.send(
            embed=discord.Embed(
                title="📌 커미션 진행",
                description=(
                    f"👨‍💻 담당 디자이너 : {designer_name}\n\n"
                    "📌 상태 : 🟢 상담중\n"
                    "📊 진행률 : 0%\n"
                    "⏰ 예상 완료 : 미설정"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
        )

        await ticket_channel.send(
            "💳 결제는 담당 디자이너의 안내 후 진행됩니다."
        )

        await ticket_channel.send(
            embed=discord.Embed(
                title="📌 추가 요구사항",
                description=(
                    "참고 이미지와 추가 요구사항을 자유롭게 작성해주세요.\n\n"
                    "• 원하는 포즈\n"
                    "• 참고 이미지\n"
                    "• 추가 텍스트\n"
                    "• 배경 세부사항\n"
                    "• 기타 요청사항"
                ),
                color=discord.Color.blurple(),
                timestamp=datetime.now()
            ),
            view=TicketCloseView()
        )

        log_channel = discord.utils.get(
            guild.text_channels,
            name=LOG_CHANNEL_NAME
        )

        if log_channel:
            await log_channel.send(
                f"📩 새로운 GFX 티켓 생성\n"
                f"{ticket_channel.mention}\n"
                f"신청자 : {user.mention}"
            )

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                try:
                    await developer.send(
                        f"🔔 새로운 GFX 커미션이 들어왔습니다.\n"
                        f"{ticket_channel.mention}"
                    )

                    await developer.send(
                        "📊 진행률 관리",
                        view=ProgressView(progress_message)
                    )

                    await developer.send(
                        "💳 결제 정보 전송",
                        view=PaymentView(ticket_channel)
                    )

                except Exception:
                    pass

        await interaction.followup.send(
            f"✅ 신청 완료!\n{ticket_channel.mention}",
            ephemeral=True
        )