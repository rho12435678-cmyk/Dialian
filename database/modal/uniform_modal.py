import discord

class UniformModal(discord.ui.Modal, title="👕 Roblox 복장 커미션 신청서"):

    content = discord.ui.TextInput(
        label="원하는 복장 내용을 작성해주세요",
        style=discord.TextStyle.paragraph,
        placeholder="원하는 디자인, 색상, 참고사항 등을 자유롭게 작성해주세요.",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        