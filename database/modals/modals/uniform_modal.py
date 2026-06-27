import discord

class UniformModal(discord.ui.Modal, title="👕 Roblox 복장 커미션 신청서"):

    roblox_name = discord.ui.TextInput(
        label="로블록스 닉네임",
        placeholder="예: Builderman",
        required=True,
        max_length=30
    )

    uniform_style = discord.ui.TextInput(
        label="원하는 복장 스타일",
        placeholder="예: 경찰, 군인, 학교 교복 등",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    extra_request = discord.ui.TextInput(
        label="추가 요청사항",
        placeholder="없으면 '없음' 입력",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):

        await interaction.response.send_message(
            "✅ 복장 신청서가 제출되었습니다.\n\n이제 티켓이 생성될 예정입니다.",
            ephemeral=True
        )
