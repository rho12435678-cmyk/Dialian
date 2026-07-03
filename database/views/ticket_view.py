import discord

from database.views.category_view import CategoryView
from database.views.ticket_guard import block_if_ticket_exists


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 티켓 생성",
        style=discord.ButtonStyle.green,
        custom_id="open_ticket"
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if await block_if_ticket_exists(interaction):
            return

        await interaction.response.send_message(
            "원하시는 커미션을 선택해주세요.",
            view=CategoryView(),
            ephemeral=True
        )
