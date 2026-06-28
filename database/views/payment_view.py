import discord
from config import PAYMENTS


class PaymentView(discord.ui.View):

    def __init__(self, ticket_channel: discord.TextChannel, designer_id: int):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel
        self.designer_id = designer_id

    @discord.ui.button(
        label="💳 결제 정보 보내기",
        style=discord.ButtonStyle.success
    )
    async def payment(self, interaction: discord.Interaction, button: discord.ui.Button):

        info = PAYMENTS[self.designer_id]

        embed = discord.Embed(
            title="💳 결제 정보",
            description=(
                f"🏦 {info['bank']}\n"
                f"계좌 : {info['account']}\n"
                f"예금주 : {info['owner']}\n\n"
                "✅ 입금 후 담당 디자이너에게 말씀해주세요."
            ),
            color=discord.Color.green()
        )

        await self.ticket_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ 결제 정보를 티켓에 전송했습니다.",
            ephemeral=True
        )