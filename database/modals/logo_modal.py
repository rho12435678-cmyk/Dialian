import discord

class LogoModal(discord.ui.Modal, title="🖌 Discord 로고 커미션"):

    style = discord.ui.TextInput(
        label="원하는 로고 스타일",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):

        await interaction.response.send_message(
            "로고 커미션은 다음 단계에서 자동으로 담당 디자이너에게 배정됩니다.",
            ephemeral=True
        )
