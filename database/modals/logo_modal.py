import discord

class LogoModal(discord.ui.Modal, title="🖌 Discord 로고 커미션 신청서"):

    logo_name = discord.ui.TextInput(
        label="로고에 들어갈 이름",
        placeholder="예: Dialian",
        required=True,
        max_length=50
    )

    logo_style = discord.ui.TextInput(
        label="원하는 로고 스타일",
        placeholder="예: 심플, 네온, 다크, 3D 등",
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
            "✅ 로고 신청서가 제출되었습니다.\n\n이제 티켓이 생성될 예정입니다.",
            ephemeral=True
        )
