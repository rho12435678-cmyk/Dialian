import discord
import aiosqlite
from database.database import DATABASE
from database.views.close_ticket import has_designer_role

class ClaimTicketView(discord.ui.View):
    def __init__(self, is_claimed: bool = False):
        super().__init__(timeout=None)
        if is_claimed:
            for child in self.children:
                child.disabled = True

    @discord.ui.button(
        label="내가 담당하기",
        style=discord.ButtonStyle.success,
        emoji="🙋‍♂️",
        custom_id="claim_ticket_button"
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        
        # 권한 확인 (관리자 또는 디자이너 역할)
        if not (member.guild_permissions.administrator or has_designer_role(member)):
            return await interaction.response.send_message(
                "❌ 디자이너 전용 기능입니다.",
                ephemeral=True
            )

        channel = interaction.channel

        # DB 검증 (이미 담당자가 있는지)
        async with aiosqlite.connect(DATABASE) as db:
            cursor = await db.execute("SELECT designer_id FROM commissions WHERE ticket_channel = ?", (channel.id,))
            row = await cursor.fetchone()
            
            if row and row[0] is not None:
                return await interaction.response.send_message(
                    f"❌ 이미 다른 디자이너(<@{row[0]}>)가 담당으로 지정되었습니다.",
                    ephemeral=True
                )

            # DB 담당자 갱신
            await db.execute(
                "UPDATE commissions SET designer_id = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_channel = ?",
                (member.id, channel.id)
            )
            await db.commit()

        # 디자이너 채널 권한 추가
        await channel.set_permissions(member, read_messages=True, send_messages=True, attach_files=True)

        # 진행 임베드 갱신
        async for msg in channel.history(limit=20, oldest_first=True):
            if msg.author == interaction.client.user and msg.embeds:
                embed = msg.embeds[0]
                if embed.title == "📌 커미션 진행":
                    lines = embed.description.splitlines() if embed.description else []
                    new_lines = []
                    for line in lines:
                        if "담당 디자이너" in line:
                            new_lines.append(f"👨‍💻 담당 디자이너 : {member.mention}")
                        else:
                            new_lines.append(line)
                    
                    embed.description = "\n".join(new_lines)
                    
                    button.disabled = True
                    button.label = f"담당자: {member.display_name}"
                    await msg.edit(embed=embed, view=self)
                    break

        await interaction.response.send_message(
            f"✅ {member.mention} 님이 해당 커미션의 담당 디자이너로 배정되었습니다!",
            ephemeral=False
        )
