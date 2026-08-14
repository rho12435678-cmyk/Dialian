import discord
import aiosqlite
from database.database import DATABASE
from database.views.close_ticket import has_designer_role, TicketCloseView
from database.views.payment_view import PaymentView


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
            
            if row and row[0] is not None and row[0] != 0:
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
        await channel.set_permissions(member, read_messages=True, send_messages=True, attach_files=True, view_channel=True)

        # 1. 버튼 상태 업데이트
        button.disabled = True
        button.label = f"담당자: {member.display_name}"
        button.style = discord.ButtonStyle.secondary

        # 2. 신청서 임베드의 '담당 디자이너' 항목 갱신
        message = interaction.message
        if message and message.embeds:
            embed = message.embeds[0]
            for i, field in enumerate(embed.fields):
                if "담당 디자이너" in field.name:
                    embed.set_field_at(i, name=field.name, value=member.mention, inline=field.inline)
                    break
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            f"✅ {member.mention} 님이 해당 커미션의 담당 디자이너로 배정되었습니다!"
        )

        # 3. 📩 담당 디자이너 DM으로 관리 패널 전송 (결제 관리 & 티켓 종료)
        try:
            await member.send(f"🔔 {channel.mention} 티켓의 담당자로 배정되었습니다.")
            await member.send(
                f"💳 결제 및 티켓 관리\n티켓: {channel.mention}\nID: {channel.id}",
                view=PaymentView(channel, member.id)
            )
            await member.send(
                f"🔒 티켓 종료 / 🗑️ 티켓 삭제\n티켓: {channel.mention}\nID: {channel.id}",
                view=TicketCloseView(channel)
            )
            
        except discord.Forbidden:
            await channel.send(
                f"⚠️ {member.mention} 님의 DM이 닫혀 있어 채널에 관리 버튼을 전송합니다.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )
            await channel.send(
                "💳 결제 및 티켓 관리",
                view=PaymentView(channel, member.id)
            )
            await channel.send(
                "🔒 티켓 종료 / 🗑️ 티켓 삭제",
                view=TicketCloseView(channel)
            )
        except Exception as e:
            print(f"[DM 패널 발송 오류]: {e}")
