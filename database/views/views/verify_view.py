import discord

VERIFY_ROLE = 1505074732700008531   # 인증 역할 ID

class VerifyView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ 인증하기",
        style=discord.ButtonStyle.success
    )
    async def verify(self, interaction: discord.Interaction, button):

        role = interaction.guild.get_role(VERIFY_ROLE)

        if role not in interaction.user.roles:
            await interaction.user.add_roles(role)

        await interaction.response.send_message(
            "✅ 인증되었습니다.",
            ephemeral=True
        )
