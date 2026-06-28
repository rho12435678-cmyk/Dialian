import discord


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 티켓 생성",
        style=discord.ButtonStyle.blurple,
        custom_id="open_ticket"
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        # 여기에 원래 티켓 생성 코드가 들어갑니다.
        pass