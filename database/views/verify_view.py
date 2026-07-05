import discord

from config import CUSTOMER_ROLE_ID

class VerifyView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ 인증하기",
        style=discord.ButtonStyle.success,
        custom_id="verify_button"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):

        role = interaction.guild.get_role(CUSTOMER_ROLE_ID)

        if role is None:
            return await interaction.response.send_message(
                "❌ 인증 역할을 찾을 수 없습니다.",
                ephemeral=True
            )

        try:
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role)
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ 봇 역할 권한을 확인해주세요.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "✅ 인증되었습니다.",
            ephemeral=True
        )
