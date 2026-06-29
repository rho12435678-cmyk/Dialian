import discord


class DeliveryView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📦 완성작 전달",
        style=discord.ButtonStyle.success,
        custom_id="deliver_work"
    )
    async def deliver(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "📁 완성작을 이 티켓 채널에 업로드해주세요.\n\n"
            "PNG, JPG, ZIP, RBLX 파일 등 모두 가능합니다.\n"
            "손님이 다운로드를 완료하면 티켓을 종료해주세요.",
            ephemeral=True
        )