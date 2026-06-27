import discord


class ProgressView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="0%", style=discord.ButtonStyle.secondary)
    async def p0(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_progress(interaction, 0, "🟢 상담중", "미설정")

    @discord.ui.button(label="25%", style=discord.ButtonStyle.secondary)
    async def p25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_progress(interaction, 25, "🟡 작업 시작", "3일")

    @discord.ui.button(label="50%", style=discord.ButtonStyle.primary)
    async def p50(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_progress(interaction, 50, "🟠 작업중", "2일")

    @discord.ui.button(label="75%", style=discord.ButtonStyle.success)
    async def p75(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_progress(interaction, 75, "🔵 마무리 작업", "1일")

    @discord.ui.button(label="100%", style=discord.ButtonStyle.success)
    async def p100(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_progress(interaction, 100, "✅ 완료", "완료")

    async def update_progress(self, interaction, progress, status, estimate):

        embed = interaction.message.embeds[0]

        embed.description = (
            f"👨‍💻 담당 디자이너 : {embed.description.splitlines()[0].replace('👨‍💻 담당 디자이너 : ','')}\n\n"
            f"📌 상태 : {status}\n"
            f"📊 진행률 : {progress}%\n"
            f"⏰ 예상 완료 : {estimate}"
        )

        await interaction.message.edit(
            embed=embed,
            view=self
        )

        await interaction.response.send_message(
            "진행률이 변경되었습니다.",
            ephemeral=True
        )
