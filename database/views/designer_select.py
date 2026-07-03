import discord

from config import DESIGNER_ROLE_IDS
from database.modal.gfx_modal import PurchaseModal
from database.modal.logo_modal import LogoModal
from database.modal.uniform_modal import UniformModal
from database.views.ticket_guard import block_if_ticket_exists


MODALS = {
    "gfx": PurchaseModal,
    "logo": LogoModal,
    "uniform": UniformModal,
}


CATEGORY_LABELS = {
    "gfx": "GFX",
    "logo": "로고",
    "uniform": "Roblox 복장",
}


def get_role_designers(guild: discord.Guild, category: str):
    role_id = DESIGNER_ROLE_IDS.get(category)

    if not role_id:
        return []

    role = guild.get_role(role_id)

    if role is None:
        return []

    return [
        member
        for member in role.members
        if not member.bot
    ]


def get_designer_options(guild: discord.Guild, category: str):
    return [
        discord.SelectOption(
            label=member.display_name,
            value=str(member.id)
        )
        for member in get_role_designers(guild, category)[:25]
    ]


class DesignerSelect(discord.ui.Select):

    def __init__(self, guild: discord.Guild, category: str):
        self.category = category

        label = CATEGORY_LABELS[category]

        super().__init__(
            placeholder=f"담당 {label} 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=get_designer_options(guild, category)
        )

    async def callback(self, interaction: discord.Interaction):
        if await block_if_ticket_exists(interaction):
            return

        modal = MODALS[self.category]()
        modal.selected_designer = int(self.values[0])

        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):

    def __init__(self, guild: discord.Guild, category: str):
        super().__init__(timeout=180)

        self.add_item(DesignerSelect(guild, category))
