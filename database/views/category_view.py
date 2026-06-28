import discord

from config import DESIGNERS
from database.modal.gfx_modal import PurchaseModal


class DesignerSelect(discord.ui.Select):

    def __init__(self, guild: discord.Guild):

        options = []

        for dev_id in DESIGNERS["gfx"].keys():
            member = guild.get_member(dev_id)

            if member:
                label = member.display_name
            else:
                label = f"알 수 없는 디자이너 ({dev_id})"

            options.append(
                discord.SelectOption(
                    label=label,
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

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(DesignerSelect(guild))