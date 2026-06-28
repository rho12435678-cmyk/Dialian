import discord

from database.views.designer_select import DesignerView


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
        await interaction.response.send_message(
            "담당 디자이너를 선택해주세요.",
            view=DesignerView(),
            ephemeral=True
        )