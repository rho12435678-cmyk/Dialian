import discord

from views.designer_select import DesignerView
from modals.logo_modal import LogoModal
from views.designer_select import DesignerView


class CommissionSelect(discord.ui.Select):

    def __init__(self):

        options = [

            discord.SelectOption(
                label="🎨 Roblox GFX",
                value="gfx",
                description="GFX 커미션 신청"
            ),

            discord.SelectOption(
                label="👕 Roblox Uniform",
                value="uniform",
                description="복장 커미션 신청"
            ),

            discord.SelectOption(
                label="🖌 Discord Logo",
                value="logo",
                description="로고 커미션 신청"
            )

        ]

        super().__init__(
            placeholder="커미션 종류를 선택해주세요.",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        if self.values[0] == "gfx":

            await interaction.response.send_message(
                "🎨 담당 디자이너를 선택해주세요.",
                view=DesignerView(),
                ephemeral=True
            )

        elif self.values[0] == "uniform":

            await interaction.response.send_modal(
                UniformModal()
            )

        elif self.values[0] == "logo":

            await interaction.response.send_modal(
                LogoModal()
            )


class TicketOpenView(discord.ui.View):

    def __init__(self):

        super().__init__(timeout=None)

        self.add_item(
            CommissionSelect()
        )
