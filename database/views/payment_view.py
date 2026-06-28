import discord
import aiosqlite


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

        async with aiosqlite.connect("data/dialian.db") as db:

            cursor = await db.execute(
                """
                SELECT bank_name, account_number, holder
                FROM bank_accounts
                WHERE developer_id = ?
                """,
                (self.designer_id,)
            )

            data = await cursor.fetchone()

        if data is None:
            return await interaction.response.send_message(
                "❌ 담당 디자이너의 계좌가 등록되어 있지 않습니다.",
                ephemeral=True
            )

        bank_name, account_number, holder = data

        embed = discord.Embed(
            title="💳 결제 정보",
            description=(
                f"🏦 {bank_name}\n"
                f"계좌번호 : `{account_number}`\n"
                f"예금주 : **{holder}**\n\n"
                "✅ 입금 후 담당 디자이너에게 말씀해주세요."
            ),
            color=discord.Color.green()
        )

        await self.ticket_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ 결제 정보를 티켓에 전송했습니다.",
            ephemeral=True
        )