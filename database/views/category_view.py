import discord

from database.views.designer_select import DesignerView
from database.views.designer_select import get_designer_options
from database.views.ticket_guard import block_if_ticket_exists
from database.modal.developer_apply_modal import DeveloperApplyModal


class CategoryView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(
        label="🎨 GFX",
        style=discord.ButtonStyle.primary
    )
    async def gfx(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_designer_select(interaction, "gfx", "GFX")

    @discord.ui.button(
        label="👕 Roblox 복장",
        style=discord.ButtonStyle.secondary
    )
    async def uniform(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_designer_select(interaction, "uniform", "Roblox 복장")

    @discord.ui.button(
        label="🛠️ 개발자 지원",
        style=discord.ButtonStyle.secondary,
        custom_id="developer_apply"
    )
    async def developer_apply(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(DeveloperApplyModal())

    async def send_designer_select(
        self,
        interaction: discord.Interaction,
        category: str,
        label: str
    ):
        if await block_if_ticket_exists(interaction):
            return

        options = get_designer_options(interaction.guild, category)

        if not options:
            return await interaction.response.send_message(
                f"등록된 {label} 디자이너가 없습니다. 역할 설정을 확인해주세요.",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"담당 {label} 디자이너를 선택해주세요.",
            view=DesignerView(interaction.guild, category),
            ephemeral=True
        )
