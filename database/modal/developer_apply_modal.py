import discord


class DeveloperApplyModal(discord.ui.Modal, title="개발자 지원"):

    field = discord.ui.TextInput(
        label="지원 분야",
        placeholder="예: GFX / Roblox 복장",
        required=True,
        max_length=30
    )

    experience = discord.ui.TextInput(
        label="경력",
        placeholder="예: 2년 / 없음",
        required=True,
        max_length=100
    )

    portfolio = discord.ui.TextInput(
        label="포트폴리오",
        placeholder="작품 링크 또는 Discord 첨부 예정",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):

        embed = discord.Embed(
            title="🛠️ 개발자 지원서",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="지원자",
            value=interaction.user.mention,
            inline=False
        )

        embed.add_field(
            name="지원 분야",
            value=self.field.value,
            inline=False
        )

        embed.add_field(
            name="경력",
            value=self.experience.value,
            inline=False
        )

        embed.add_field(
            name="포트폴리오",
            value=self.portfolio.value,
            inline=False
        )

        await interaction.response.send_message(
            "✅ 지원서가 제출되었습니다. 관리자가 확인 후 연락드리겠습니다.",
            ephemeral=True
        )

        await interaction.channel.send(embed=embed)
