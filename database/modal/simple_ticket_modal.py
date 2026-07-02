import discord

from datetime import datetime
from config import *
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView
from database.views.close_ticket import TicketCloseView


class SimpleTicketModal(discord.ui.Modal):

    MODAL_TITLE = "커미션 신청서"
    FORM_TITLE = "📋 커미션 신청서"
    FIELD_NAME = "내용"

    def __init__(self):
        super().__init__(title=self.MODAL_TITLE)

        self.selected_designer = None

        self.content = discord.ui.TextInput(
            label=self.FIELD_NAME,
            placeholder="원하는 내용을 자유롭게 작성해주세요.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        self.add_item(self.content)

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
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
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
            overwrites=overwrites,
            topic=str(user.id)
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
            name=self.FIELD_NAME,
            value=self.content.value,
            inline=False
        )

        await ticket_channel.send(
            content=user.mention,
            embed=embed
        )
        
        log_channel = discord.utils.get(
    guild.text_channels,
    name=LOG_CHANNEL_NAME
)

if log_channel:
    await log_channel.send(
        f"📩 새로운 로고 티켓 생성\n"
        f"{ticket_channel.mention}\n"
        f"신청자 : {user.mention}"
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

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                try:
                    await developer.send(
                        f"🔔 새로운 커미션이 들어왔습니다.\n"
                        f"{ticket_channel.mention}"
                    )

                    await developer.send(
                        "📊 진행률 관리",
                        view=ProgressView(progress_message)
                    )

                    await developer.send(
                        "💳 결제 정보 전송",
                        view=PaymentView(ticket_channel, self.selected_designer)
                    )

                    await developer.send(
                        "🔒 티켓 관리",
                        view=TicketCloseView(ticket_channel)
                    )

                except Exception as e:
                    print(e)

        await interaction.followup.send(
            f"✅ 신청 완료!\n{ticket_channel.mention}",
            ephemeral=True
        )
