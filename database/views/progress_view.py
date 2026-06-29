import discord

from database.views.delivery_view import DeliveryView

DESIGNERS = [
    375938495350571009,
    1292859064065458189,
    1468584582113919129,
    1465051418162626763,
]


class ProgressView(discord.ui.View):
    def __init__(self, progress_message=None):
        super().__init__(timeout=None)
        self.progress_message = progress_message

    @discord.ui.button(label="0%", style=discord.ButtonStyle.secondary)
    async def p0(self, interaction, button):
        await self.update_progress(interaction, 0, "🟢 상담중", "미설정")

    @discord.ui.button(label="25%", style=discord.ButtonStyle.secondary)
    async def p25(self, interaction, button):
        await self.update_progress(interaction, 25, "🟡 작업 시작", "3일")

    @discord.ui.button(label="50%", style=discord.ButtonStyle.primary)
    async def p50(self, interaction, button):
        await self.update_progress(interaction, 50, "🟠 작업중", "2일")

    @discord.ui.button(label="75%", style=discord.ButtonStyle.success)
    async def p75(self, interaction, button):
        await self.update_progress(interaction, 75, "🔵 마무리 작업", "1일")

    @discord.ui.button(label="100%", style=discord.ButtonStyle.success)
    async def p100(self, interaction, button):
        await self.update_progress(interaction, 100, "✅ 완료", "완료")

    async def update_progress(self, interaction, progress, status, estimate):

        if interaction.user.id not in DESIGNERS:
            return await interaction.response.send_message(
                "❌ 디자이너만 사용할 수 있습니다.",
                ephemeral=True
            )

        embed = self.progress_message.embeds[0]

        designer = embed.description.splitlines()[0].replace(
            "👨‍💻 담당 디자이너 : ", ""
        )

        embed.description = (
            f"👨‍💻 담당 디자이너 : {designer}\n\n"
            f"📌 상태 : {status}\n"
            f"📊 진행률 : {progress}%\n"
            f"⏰ 예상 완료 : {estimate}"
        )

        await self.progress_message.edit(embed=embed)

        await interaction.response.send_message(

if progress == 100:

    await interaction.channel.send(
        embed=discord.Embed(
            title="📦 작업이 완료되었습니다!",
            description=(
                "담당 디자이너는 아래 버튼을 눌러 "
                "완성작을 전달해주세요."
            ),
            color=discord.Color.green()
        ),
        view=DeliveryView()
    )

            "✅ 진행률을 변경했습니다.",
            ephemeral=True
        )
