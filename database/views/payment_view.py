import discord


class PaymentView(discord.ui.View):

    def __init__(self, ticket_channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel

    @discord.ui.button(
        label="💳 결제 정보 보내기",
        style=discord.ButtonStyle.success
    )
    async def payment(self, interaction: discord.Interaction, button: discord.ui.Button):

        embed = discord.Embed(
            title="💳 결제 정보",
            description=(
                "🏦 국민은행\n"
                "123-456-789012\n"
                "예금주 : 홍길동\n\n"
                "✅ 입금 후 담당 디자이너에게 말씀해주세요."
            ),
            color=discord.Color.green()
        )

        # 티켓 채널에 결제 정보 전송
        await self.ticket_channel.send(embed=embed)

        # 개발자 DM에는 성공 메시지만
        await interaction.response.send_message(
            "✅ 티켓에 결제 정보를 전송했습니다.",
            ephemeral=True
        )