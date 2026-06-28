import discord

from config import DESIGNERS


class PaymentView(discord.ui.View):

    def __init__(self, designer_id: int):
        super().__init__(timeout=None)
        self.designer_id = designer_id

    @discord.ui.button(
        label="💳 결제 정보 보기",
        style=discord.ButtonStyle.success
    )
    async def payment(self, interaction: discord.Interaction, button: discord.ui.Button):

        embed = discord.Embed(
            title="💳 결제 정보",
            description=(
                "계좌 등록 기능을 연결하면\n"
                "여기에 자동으로 계좌가 표시됩니다."
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )
