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
                    value=dev_id
                )
            )

        super().__init__(
            placeholder="담당 GFX 디자이너를 선택해주세요.",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        modal = PurchaseModal()

        modal.selected_designer = int(self.values[0])

        await interaction.response.send_modal(modal)


class DesignerSelectView(discord.ui.View):

    def __init__(self):

        super().__init__(timeout=300)

        self.add_item(
            DesignerSelect()
        )
