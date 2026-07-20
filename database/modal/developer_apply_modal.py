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

    program = discord.ui.TextInput(
        label="사용 가능 프로그램",
        placeholder="예: Blender, Photoshop",
        required=True,
        max_length=100,
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
            name="사용 가능 프로그램",
            value=self.program.value,
            inline=False,
        )

        await interaction.response.send_message(
            "✅ 지원서가 제출되었습니다. 관리자가 확인 후 연락드리겠습니다.",
            ephemeral=True
        )

        await interaction.channel.send(embed=embed)
