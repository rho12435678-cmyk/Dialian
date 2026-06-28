import discord


class LogoModal(discord.ui.Modal, title="🖌 로고 커미션 신청서"):

    content = discord.ui.TextInput(
        label="원하는 로고 내용을 작성해주세요",
        style=discord.TextStyle.paragraph,
        placeholder="원하는 색상, 분위기, 참고사항 등을 자유롭게 작성해주세요.",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        pass