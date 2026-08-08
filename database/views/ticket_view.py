import discord
import aiosqlite
from discord.ui import View, button, Modal, TextInput
from database.database import DATABASE
from database.views.category_view import CategoryView
from database.views.ticket_guard import block_if_ticket_exists


# --------------------------------------------------
# 1. 메인 티켓 오픈 View (기존 코드)
# --------------------------------------------------
class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 티켓 생성",
        style=discord.ButtonStyle.green,
        custom_id="open_ticket"
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if await block_if_ticket_exists(interaction):
            return

        await interaction.response.send_message(
            "원하시는 커미션 또는 지원 항목을 선택해주세요.",
            view=CategoryView(),
            ephemeral=True
        )


# --------------------------------------------------
# 2. DM 전용 모달 (진행률 설정 / 상태 변경)
# --------------------------------------------------
class ProgressModal(Modal, title="📊 진행률 설정"):
    progress = TextInput(
        label="진행률 (%)",
        placeholder="0~100 사이 숫자만 입력해주세요 (예: 50)",
        min_length=1,
        max_length=3,
        required=True
    )

    def __init__(self, ticket_channel_id: int):
        super().__init__()
        self.ticket_channel_id = ticket_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        val = self.progress.value.strip().replace("%", "")
        if not val.isdigit() or not (0 <= int(val) <= 100):
            return await interaction.response.send_message("❌ 0에서 100 사이의 숫자를 입력해 주세요.", ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE commissions SET progress = ? WHERE ticket_channel = ?",
                (f"{val}%", channel.id)
            )
            await db.commit()

        await channel.send(f"📊 **{interaction.user.mention}** 님이 진행률을 **{val}%**로 변경했습니다.")
        await interaction.response.send_message(f"✅ 진행률이 **{val}%**로 변경되었습니다.", ephemeral=True)


class StatusModal(Modal, title="📌 커미션 상태 변경"):
    status_text = TextInput(
        label="상태 내용",
        placeholder="예: 작업 시작, 작업 중, 마무리, 완료",
        required=True
    )

    def __init__(self, ticket_channel_id: int):
        super().__init__()
        self.ticket_channel_id = ticket_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        status = self.status_text.value.strip()

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE commissions SET status = ? WHERE ticket_channel = ?",
                (status, channel.id)
            )
            await db.commit()

        await channel.send(f"📌 **{interaction.user.mention}** 님이 상태를 변경했습니다.\n**상태:** `{status}`")
        await interaction.response.send_message(f"✅ 상태가 `{status}`(으)로 연동되었습니다.", ephemeral=True)


# --------------------------------------------------
# 3. 디자이너 DM 전용 컨트롤 패널 View
# --------------------------------------------------
class DesignerDMControlView(View):
    def __init__(self, ticket_channel_id: int):
        super().__init__(timeout=None)
        self.ticket_channel_id = ticket_channel_id

    @button(label="📊 진행률 설정", style=discord.ButtonStyle.primary, custom_id="dm_btn_progress")
    async def set_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProgressModal(self.ticket_channel_id))

    @button(label="💳 계좌 전송", style=discord.ButtonStyle.success, custom_id="dm_btn_account")
    async def send_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await channel.send("💳 **입금 계좌 안내**\n`카카오뱅크 3333-XX-XXXXXX (예금주: Dial)`\n입금 후 입금자명을 채널에 남겨주세요!")
        await interaction.response.send_message("✅ 티켓 채널에 계좌 안내를 전송했습니다.", ephemeral=True)

    @button(label="📌 상태 변경", style=discord.ButtonStyle.secondary, custom_id="dm_btn_status")
    async def change_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal(self.ticket_channel_id))

    @button(label="✅ 작업 완료", style=discord.ButtonStyle.success, custom_id="dm_btn_complete")
    async def complete_job(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        embed = discord.Embed(
            title="📦 작업이 완료되었습니다!",
            description="작업이 완료되었습니다. 완성작을 전달해주세요.",
            color=0x2ECC71
        )
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ 티켓 채널에 작업 완료 알림을 전송했습니다.", ephemeral=True)

    @button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="dm_btn_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await channel.send("🔒 **디자이너 요청으로 5초 후 티켓이 종료됩니다.**")
        await interaction.response.send_message("✅ 티켓 종료 안내 메시지를 전송했습니다.", ephemeral=True)
