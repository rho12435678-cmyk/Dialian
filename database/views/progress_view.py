import re
import aiosqlite
import discord
from discord import ui
from database.database import DATABASE
from config import DESIGNER_ROLE_IDS


def has_designer_role(member):
    if member is None:
        return False
    role_ids = {role_id for role_id in DESIGNER_ROLE_IDS.values() if role_id}
    return any(role.id in role_ids for role in member.roles)


class ProgressView(ui.View):
    def __init__(self, designer_id: int = None, active_progress: int = 0):
        super().__init__(timeout=None)
        self.designer_id = int(designer_id) if designer_id else None
        self.active_progress = active_progress
        self.mark_active_progress()

    def mark_active_progress(self):
        """현재 진행률에 맞춰 버튼의 색상과 라벨 상태를 동적으로 변경합니다."""
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if not item.custom_id or not item.custom_id.startswith("progress_"):
                continue

            try:
                progress = int(item.custom_id.replace("progress_", ""))
                item.label = f"✓ {progress}%" if progress == self.active_progress else f"{progress}%"
                item.style = (
                    discord.ButtonStyle.success
                    if progress == self.active_progress
                    else discord.ButtonStyle.secondary
                )
            except ValueError:
                pass

    def extract_channel_id_and_guild(self, message: discord.Message):
        """DM 메시지의 임베드 설명에서 티켓 채널 ID와 서버 객체를 안전하게 추출합니다."""
        if not message.embeds:
            return None, None
            
        embed = message.embeds[0]
        desc = embed.description or ""
        
        # 🏷️ 티켓 채널 : <#채널ID> 형태에서 채널 ID 추출
        match = re.search(r"<#(\d+)>", desc)
        if not match:
            return None, None
            
        channel_id = int(match.group(1))
        
        # 임베드 제목이나 텍스트에서 서버 이름/정보를 통해 Guild 객체 탐색 (bot 인스턴스 활용)
        guild = message._state._get_client().guilds[0] # 기본 Fallback 혹은 연동된 봇 클라이언트 이용
        # 정확한 매칭을 위해 봇이 속한 모든 길드 중 해당 채널을 가지고 있는 길드 탐색
        for g in message._state._get_client().guilds:
            if g.get_channel(channel_id):
                guild = g
                break
                
        return channel_id, guild

    async def update_progress(self, interaction: discord.Interaction, progress: int, status: str, estimate: str):
        # 1. DM 메시지로부터 대상 티켓 채널 ID 및 길드 파악
        channel_id, guild = self.extract_channel_id_and_guild(interaction.message)
        if not channel_id or not guild:
            return await interaction.response.send_message(
                "❌ 이 패널에 연결된 티켓 채널 정보를 찾을 수 없습니다.", 
                ephemeral=True
            )

        # 2. 권한 검증 (담당 디자이너 또는 관리자)
        guild_member = guild.get_member(interaction.user.id)
        if not guild_member:
            try:
                guild_member = await guild.fetch_member(interaction.user.id)
            except Exception:
                pass

        is_admin = guild_member and guild_member.guild_permissions.administrator
        is_assigned_designer = self.designer_id is not None and interaction.user.id == self.designer_id
        is_designer_fallback = self.designer_id is None and has_designer_role(guild_member)

        if not (is_admin or is_assigned_designer or is_designer_fallback):
            return await interaction.response.send_message(
                "❌ 담당 디자이너 또는 관리자만 사용할 수 있습니다.",
                ephemeral=True
            )

        ticket_channel = guild.get_channel(channel_id)
        
        # 3. 데이터베이스에서 기존 상태 확인
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT progress, status FROM commissions WHERE ticket_channel = ?", 
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                
        already_completed = row and row[0] == 100

        # 4. 디자이너 DM에 있는 패널 UI 및 텍스트 갱신
        try:
            old_embed = interaction.message.embeds[0]
            new_embed = discord.Embed.from_dict(old_embed.to_dict())
            
            desc = new_embed.description or ""
            # 상태, 진행률, 예상 완료 시간 텍스트 정규식으로 교체
            desc = re.sub(r"📌 상태 : .*", f"📌 상태 : {status}", desc)
            desc = re.sub(r"📊 진행률 : \d+%", f"📊 진행률 : {progress}%", desc)
            desc = re.sub(r"⏰ 예상 완료 : .*", f"⏰ 예상 완료 : {estimate}", desc)
            new_embed.description = desc

            # 새로운 진행률 상태가 반영된 View로 교체 생성하여 메시지 수정
            new_view = ProgressView(designer_id=self.designer_id, active_progress=progress)
            await interaction.message.edit(embed=new_embed, view=new_view)
        except Exception as e:
            print(f"[DM 패널 갱신 오류] {e}")

        # 5. DB 업데이트 수행
        now = datetime.now().isoformat()
        status_value = "completed" if progress == 100 else "in_progress"
        completed_at = now if progress == 100 and not already_completed else None

        async with aiosqlite.connect(DATABASE) as db:
            if completed_at:
                await db.execute(
                    """
                    UPDATE commissions
                    SET progress = ?, status = ?, completed_at = ?, updated_at = ?
                    WHERE ticket_channel = ?
                    """,
                    (progress, status_value, completed_at, now, channel_id)
                )
            else:
                await db.execute(
                    """
                    UPDATE commissions
                    SET progress = ?, status = ?, updated_at = ?
                    WHERE ticket_channel = ?
                    """,
                    (progress, status_value, now, channel_id)
                )
            await db.commit()

        # 6. 실제 서버의 티켓 채널에 알림 전송
        if ticket_channel:
            await ticket_channel.send(
                f"📊 디자이너가 작업 진행률을 **{progress}%**로 변경했습니다.\n"
                f"상태: {status}"
            )

            if progress == 100 and not already_completed:
                await ticket_channel.send(
                    embed=discord.Embed(
                        title="📦 작업이 완료되었습니다!",
                        description="작업이 완료되었습니다. 완성작을 전달해주세요.",
                        color=discord.Color.green()
                    )
                )

        await interaction.response.send_message(
            f"✅ 진행률이 **{progress}%**로 성공적으로 반영되었습니다.",
            ephemeral=True
        )

    @ui.button(label="0%", style=discord.ButtonStyle.secondary, custom_id="progress_0")
    async def p0(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_progress(interaction, 0, "🟢 상담중", "미설정")

    @ui.button(label="25%", style=discord.ButtonStyle.secondary, custom_id="progress_25")
    async def p25(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_progress(interaction, 25, "🟡 작업 시작", "3일")

    @ui.button(label="50%", style=discord.ButtonStyle.primary, custom_id="progress_50")
    async def p50(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_progress(interaction, 50, "🟠 작업중", "2일")

    @ui.button(label="75%", style=discord.ButtonStyle.success, custom_id="progress_75")
    async def p75(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_progress(interaction, 75, "🔵 마무리 작업", "1일")

    @ui.button(label="100%", style=discord.ButtonStyle.success, custom_id="progress_100")
    async def p100(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_progress(interaction, 100, "✅ 완료", "완료")
