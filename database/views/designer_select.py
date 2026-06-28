import discord

from config import DESIGNERS
from database.modal.gfx_modal import PurchaseModal
from database.modal.logo_modal import LogoModal
from database.modal.uniform_modal import UniformModal


class DesignerSelect(discord.ui.Select):

    def __init__(self):

        options = []

        for dev_id, name in DESIGNERS["gfx"].items():
            options.append(
                discord.SelectOption(
                    label=name,
                    value=str(dev_id)
                )
            )

        super().__init__(
            placeholder="담당 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        designer_id = int(self.values[0])

        # GFX + 로고 디자이너
        if designer_id == DESIGNERS["logo"]["id"]:
            modal = LogoModal()

        # GFX + 복장 디자이너
        elif designer_id == DESIGNERS["uniform"]["id"]:
            modal = UniformModal()

        # 일반 GFX
        else:
            modal = PurchaseModal()

        modal.selected_designer = designer_id

        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(DesignerSelect())