import discord
import aiosqlite

from datetime import datetime
from database.database import DATABASE
from config import DESIGNER_ROLE_IDS
from database.views.ticket_context import resolve_ticket_channel


def has_designer_role(member):
    if member is None:
        return False

    role_ids = {
        role_id
        for role_id in DESIGNER_ROLE_IDS.values()
        if role_id
    }

    return any(role.id in role_ids for role in member.roles)


class ProgressView(discord.ui.View):
    def __init__(self, progress_message=None, designer_id=None, active_progress=0):
        super().__init__(timeout=None)
        self.progress_message = progress_message
        self.designer_id = int(designer_id) if designer_id else None
        self.active_progress = active_progress
        self.mark_active_progress()

    def mark_active_progress(self):
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue

            if not item.custom_id or not item.custom_id.startswith("progress_"):
                continue

            progress = int(item.custom_id.replace("progress_", ""))
            item.label = f"✓ {progress}%" if progress == self.active_progress else f"{progress}%"
            item.style = (
                discord.ButtonStyle.success
                if progress == self.active_progress
                else discord.ButtonStyle.secondary
            )

    @discord.ui.button(
        label="0%",
        style=discord.ButtonStyle.secondary,
        custom_id="progress_0"
    )
    async def p0(self, interaction, button):
        await self.update_progress(interaction, 0, "🟢 상담중", "미설정")

    @discord.ui.button(
        label="25%",
        style=discord.ButtonStyle.secondary,
        custom_id="progress_25"
    )
    async def p25(self, interaction, button):
        await self.update_progress(interaction, 25, "🟡 작업 시작", "3일")

    @discord.ui.button(
        label="50%",
        style=discord.ButtonStyle.primary,
        custom_id="progress_50"
    )
    async def p50(self, interaction, button):
        await self.update_progress(interaction, 50, "🟠 작업중", "2일")

    @discord.ui.button(
        label="75%",
        style=discord.ButtonStyle.success,
        custom_id="progress_75"
    )
    async def p75(self, interaction, button):
        await self.update_progress(interaction, 75, "🔵 마무리 작업", "1일")

    @discord.ui.button(
        label="100%",
        style=discord.ButtonStyle.success,
        custom_id="progress_100"
    )
    async def p100(self, interaction, button):
        await self.update_progress(interaction, 100, "✅ 완료", "완료")

    @discord.ui.button(label="상담중", style=discord.ButtonStyle.secondary, custom_id="status_consulting", row=1)
    async def status_consulting(self, interaction, button):
        await self.update_status(interaction, "상담중")

    @discord.ui.button(label="작업 시작", style=discord.ButtonStyle.primary, custom_id="status_started", row=1)
    async def status_started(self, interaction, button):
        await self.update_status(interaction, "작업 시작")

    @discord.ui.button(label="작업 중", style=discord.ButtonStyle.primary, custom_id="status_working", row=1)
    async def status_working(self, interaction, button):
        await self.update_status(interaction, "작업 중")

    @discord.ui.button(label="마무리", style=discord.ButtonStyle.success, custom_id="status_finishing", row=1)
    async def status_finishing(self, interaction, button):
        await self.update_status(interaction, "마무리")

    @discord.ui.button(label="완료", style=discord.ButtonStyle.success, custom_id="status_completed", row=1)
    async def status_completed(self, interaction, button):
        await self.update_status(interaction, "완료")

    async def update_status(self, interaction, status):
        if self.progress_message is None:
            ticket_channel = await resolve_ticket_channel(interaction)
            if ticket_channel is not None:
                async for message in ticket_channel.history(limit=50, oldest_first=True):
                    if message.embeds and message.embeds[0].title == "📊 커미션 진행":
                        self.progress_message = message
                        break
        if self.progress_message is None:
            return await interaction.response.send_message("진행률 메시지를 찾지 못했습니다.", ephemeral=True)

        member = self.progress_message.guild.get_member(interaction.user.id)
        allowed = (
            (member and member.guild_permissions.administrator)
            or (self.designer_id is not None and interaction.user.id == self.designer_id)
            or (self.designer_id is None and has_designer_role(member))
        )
        if not allowed:
            return await interaction.response.send_message("담당 디자이너 또는 관리자만 사용할 수 있습니다.", ephemeral=True)

        embed = self.progress_message.embeds[0]
        lines = embed.description.splitlines() if embed.description else []
        if len(lines) < 4:
            return await interaction.response.send_message("진행률 메시지 형식이 올바르지 않습니다.", ephemeral=True)
        lines[2] = f"📌 상태 : {status}"
        embed.description = "\n".join(lines)
        await self.progress_message.edit(embed=embed)
        await self.progress_message.channel.send(f"📌 {interaction.user.mention}님이 상태를 변경했습니다.\n상태: {status}")
        await interaction.response.send_message("상태를 변경했습니다.", ephemeral=True)

    async def update_progress(self, interaction, progress, status, estimate):

        if self.progress_message is None:
            ticket_channel = await resolve_ticket_channel(interaction)
            if ticket_channel is not None:
                async for message in ticket_channel.history(limit=50, oldest_first=True):
                    if message.embeds and message.embeds[0].title == "📊 커미션 진행":
                        self.progress_message = message
                        break

        guild_member = None

        if self.progress_message and self.progress_message.guild:
            guild_member = self.progress_message.guild.get_member(
                interaction.user.id
            )

        is_admin = (
            guild_member is not None
            and guild_member.guild_permissions.administrator
        )
        is_assigned_designer = (
            self.designer_id is not None
            and interaction.user.id == self.designer_id
        )
        is_designer_fallback = (
            self.designer_id is None
            and has_designer_role(guild_member)
        )

        if not (is_admin or is_assigned_designer or is_designer_fallback):
            return await interaction.response.send_message(
                "❌ 담당 디자이너 또는 관리자만 사용할 수 있습니다.",
                ephemeral=True
            )

        if self.progress_message is None:
            return await interaction.response.send_message(
                "❌ 진행 메시지를 찾을 수 없습니다.",
                ephemeral=True
            )

        embed = self.progress_message.embeds[0]
        already_completed = (
            embed.description
            and "📊 진행률 : 100%" in embed.description
        )

        designer = embed.description.splitlines()[0].replace(
            "👨‍💻 담당 디자이너 : ", ""
        )

        embed.description = (
            f"👨‍💻 담당 디자이너 : {designer}\n\n"
            f"📌 상태 : {status}\n"
            f"📊 진행률 : {progress}%\n"
            f"⏰ 예상 완료 : {estimate}"
        )

        await self.progress_message.edit(embed=embed)
        await interaction.message.edit(
            view=ProgressView(self.progress_message, self.designer_id, progress)
        )

        now = datetime.now().isoformat()
        status_value = "completed" if progress == 100 else "in_progress"
        completed_at = now if progress == 100 and not already_completed else None

        async with aiosqlite.connect(DATABASE) as db:
            if completed_at:
                await db.execute(
                    """
                    UPDATE commissions
                    SET progress = ?,
                        status = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE ticket_channel = ?
                    """,
                    (
                        progress,
                        status_value,
                        completed_at,
                        now,
                        self.progress_message.channel.id
                    )
                )
            else:
                await db.execute(
                    """
                    UPDATE commissions
                    SET progress = ?,
                        status = ?,
                        updated_at = ?
                    WHERE ticket_channel = ?
                    """,
                    (
                        progress,
                        status_value,
                        now,
                        self.progress_message.channel.id
                    )
                )

            await db.commit()

        await self.progress_message.channel.send(
            f"📊 {interaction.user.mention}님이 진행률을 {progress}%로 변경했습니다.\n"
            f"상태: {status}"
        )

        if progress == 100 and not already_completed:

            await self.progress_message.channel.send(
                embed=discord.Embed(
                    title="📦 작업이 완료되었습니다!",
                    description=(
                        "작업이 완료되었습니다. 완성작을 전달해주세요."
                    ),
                    color=discord.Color.green()
                )
            )

        await interaction.response.send_message(
            "✅ 진행률을 변경했습니다.",
            ephemeral=True
        )
