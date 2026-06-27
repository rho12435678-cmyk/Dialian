import discord

class UniformModal(discord.ui.Modal, title="👕 Roblox 복장 커미션"):

    style = discord.ui.TextInput(
        label="원하는 복장 스타일",
        placeholder="예: 경찰, 군인, 카페, 그룹복...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    reference = discord.ui.TextInput(
        label="참고 이미지 링크 (없으면 없음)",
        placeholder="https://...",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):

        await interaction.response.send_message(
            "✅ 복장 신청서가 접수되었습니다.",
            ephemeral=True
        )
