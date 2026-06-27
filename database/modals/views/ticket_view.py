import discord

class CommissionSelect(discord.ui.Select):

    def __init__(self):

        options = [

            discord.SelectOption(
                label="Roblox GFX",
                emoji="🎨",
                description="GFX 커미션 신청",
                value="gfx"
            ),

            discord.SelectOption(
                label="Roblox Uniform",
                emoji="👕",
                description="복장 커미션 신청",
                value="uniform"
            ),

            discord.SelectOption(
                label="Discord Logo",
                emoji="🖌",
                description="로고 커미션 신청",
                value="logo"
            )

        ]

        super().__init__(
            placeholder="커미션 종류를 선택해주세요.",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.send_message(
            f"선택한 커미션 : **{self.values[0]}**",
            ephemeral=True
        )


class TicketSelectView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(
            CommissionSelect()
        )
