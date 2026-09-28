"""Send payment details only for the current DB-assigned designer."""
import discord
import aiosqlite
from database.database import DATABASE
from database.views.ticket_context import resolve_ticket_channel
from database.services.ticket_access import ticket_assignment


class PaymentView(discord.ui.View):
    def __init__(self, ticket_channel=None, designer_id=None):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel
        # Retained only for older construction sites. NEVER use this cached ID
        # for authorizing a click, including persistent DM buttons.
        self.designer_id = int(designer_id) if designer_id else None

    @discord.ui.button(
        label="💳 결제 정보 보내기",
        style=discord.ButtonStyle.success,
        custom_id="ticket_payment",
    )
    async def payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await resolve_ticket_channel(interaction, self.ticket_channel)
        if ticket is None:
            return await interaction.response.send_message(
                "❌ 티켓을 찾지 못했습니다. 해당 티켓에서 !계좌전송을 사용해주세요.",
                ephemeral=True,
            )
        # The original server must match. Do not trust a forwarded/stale DM
        # whose stored ticket channel belongs to a different guild.
        if interaction.guild and ticket.guild.id != interaction.guild.id:
            return await interaction.response.send_message(
                "❌ 이 서버의 티켓이 아닙니다.", ephemeral=True,
            )
        assignment = await ticket_assignment(ticket)
        if (not assignment.known or assignment.legacy
                or assignment.status in ("closed", "cancelled", "closing")
                or not assignment.designer_id):
            return await interaction.response.send_message(
                "❌ 현재 유효한 담당 디자이너가 없습니다.", ephemeral=True,
            )
        # Resolve afresh per click. Old DM/persistent view IDs must never
        # authorize a previous designer after reassignment.
        if interaction.user.id != assignment.designer_id:
            return await interaction.response.send_message(
                "❌ 현재 담당 디자이너만 결제 정보를 보낼 수 있습니다.",
                ephemeral=True,
            )
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """SELECT bank_name, account_number, holder
                   FROM bank_accounts WHERE developer_id = ?""",
                (assignment.designer_id,),
            ) as cursor:
                data = await cursor.fetchone()
        if data is None:
            return await interaction.response.send_message(
                "❌ 현재 담당 디자이너의 계좌가 등록되지 않았습니다.",
                ephemeral=True,
            )
        bank_name, account_number, holder = data
        embed = discord.Embed(
            title="💳 결제 정보",
            description=(
                f"🏦 {bank_name}\n계좌번호: `{account_number}`\n"
                f"예금주: **{holder}**\n\n"
                "✅ 입금 후 담당 디자이너에게 말씀해주세요."
            ),
            color=discord.Color.green(),
        )
        # A reassignment could happen while awaiting the bank lookup.
        # Recheck before disclosing the bank details to the ticket.
        latest = await ticket_assignment(ticket)
        if (latest.designer_id != assignment.designer_id or
                latest.status in ("closed", "cancelled", "closing")):
            return await interaction.response.send_message(
                "❌ 담당자 또는 티켓 상태가 변경되어 전송을 취소했습니다.",
                ephemeral=True,
            )
        await ticket.send(embed=embed)
        await interaction.response.send_message(
            "✅ 결제 정보를 티켓에 전송했습니다.", ephemeral=True,
        )
