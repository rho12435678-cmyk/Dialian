import discord
import aiosqlite
from database.database import DATABASE
from database.views.ticket_context import resolve_ticket_channel


class PaymentView(discord.ui.View):

    def __init__(self, ticket_channel=None, designer_id=None):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel
        self.designer_id = int(designer_id) if designer_id else None

    @discord.ui.button(
        label="💳 결제 정보 보내기",
        style=discord.ButtonStyle.success,
        custom_id="ticket_payment"
    )
    async def payment(self, interaction: discord.Interaction, button: discord.ui.Button):

        ticket_channel = await resolve_ticket_channel(interaction, self.ticket_channel)
        if ticket_channel is None:
            return await interaction.response.send_message(
                "Ticket context was not found. Use !계좌전송 in the ticket channel.",
                ephemeral=True
            )

        if self.designer_id is None:
            async with aiosqlite.connect(DATABASE) as db:
                cursor = await db.execute(
                    "SELECT designer_id FROM commissions WHERE ticket_channel = ?",
                    (ticket_channel.id,)
                )
                row = await cursor.fetchone()
            self.designer_id = row[0] if row and row[0] else None

        if self.designer_id is None:
            return await interaction.response.send_message(
                "Assigned designer was not found. Use !계좌전송 in the ticket channel.",
                ephemeral=True
            )

        if interaction.user.id != self.designer_id:
            return await interaction.response.send_message(
                "❌ 담당 디자이너만 결제 정보를 보낼 수 있습니다.",
                ephemeral=True
            )

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

        await ticket_channel.send(embed=embed)

        await interaction.response.send_message(
            "✅ 결제 정보를 티켓에 전송했습니다.",
            ephemeral=True
        )
