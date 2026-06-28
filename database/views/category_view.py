import discord

from database.views.designer_select import DesignerView
from config import DESIGNERS
from database.modal.gfx_modal import PurchaseModal
from database.modal.logo_modal import LogoModal
from database.modal.uniform_modal import UniformModal


class CategoryView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(
        label="🎨 GFX",
        style=discord.ButtonStyle.primary
    )
    async def gfx(self, interaction: discord.Interaction, button: discord.ui.Button):

        await interaction.response.send_message(
            "담당 GFX 디자이너를 선택해주세요.",
            view=DesignerView(interaction.guild),
            ephemeral=True
        )

    @discord.ui.button(
        label="🖼️ 로고",
        style=discord.ButtonStyle.success
    )
    async def logo(self, interaction: discord.Interaction, button: discord.ui.Button):

        modal = LogoModal()
modal.selected_designer = list(DESIGNERS["logo"].keys())[0]
await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="👕 Roblox 복장",
        style=discord.ButtonStyle.secondary
    )
    async def uniform(self, interaction: discord.Interaction, button: discord.ui.Button):

        
modal = UniformModal()
modal.selected_designer = list(DESIGNERS["uniform"].keys())[0]
await interaction.response.send_modal(modal)