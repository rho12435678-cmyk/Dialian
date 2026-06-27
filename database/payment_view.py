import discord
import aiosqlite

DATABASE = "data/dialian.db"


class PaymentView(discord.ui.View):

    def __init__(self, designer_id: int):
        super().__init__(timeout=None)
        self.designer_id = designer_id

    @discord.ui.button(
        label="💳 계좌 보기",
        style=discord.ButtonStyle.green
    )
    async def bank_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        async with aiosqlite.connect(DATABASE) as db:

            cursor = await db.execute(
                """
                SELECT bank_name, account_number, holder
                FROM bank_accounts
                WHERE developer_id = ?
                """,
                (self.designer_id,)
            )

            account = await cursor.fetchone()

        if not account:
            return await interaction.response.send_message(
                "❌ 등록된 계좌가 없습니다.",
                ephemeral=True
            )

        bank_name, account_number, holder = account

        embed = discord.Embed(
            title="💳 결제 계좌",
            color=discord.Color.green()
        )

        embed.add_field(
            name="은행",
            value=bank_name,
            inline=False
        )

        embed.add_field(
            name="계좌번호",
            value=f"`{account_number}`",
            inline=False
        )

        embed.add_field(
            name="예금주",
            value=holder,
            inline=False
        )

        embed.set_footer(
            text="입금 후 디자이너에게 입금 확인을 요청해주세요."
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )
