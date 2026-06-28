import discord

from config import DESIGNERS
from modals.gfx_modal import PurchaseModal


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
            placeholder="담당 GFX 디자이너를 선택하세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        modal = PurchaseModal()
        modal.selected_designer = int(self.values[0])

        await interaction.response.send_modal(modal)


class DesignerView(discord.ui.View):

    def __init__(self):

        super().__init__(timeout=180)

        self.add_item(DesignerSelect())