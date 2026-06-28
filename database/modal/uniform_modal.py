import discord

class UniformModal(discord.ui.Modal, title="👕 Roblox 복장 커미션 신청서"):

    content = discord.ui.TextInput(
        label="원하는 복장 내용을 작성해주세요",
        style=discord.TextStyle.paragraph,
        placeholder="원하는 복장 디자인을 자유롭게 작성해주세요.",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        # 여기서 티켓 생성