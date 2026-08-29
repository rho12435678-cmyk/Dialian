import asyncio
import discord
import aiosqlite
from discord.ui import View, button, Modal, TextInput

from database.database import DATABASE
from database.views.category_view import CategoryView
from database.views.ticket_guard import block_if_ticket_exists


# --------------------------------------------------
# 0. 공통 손님 호출 함수
# --------------------------------------------------
async def handle_customer_call(
    channel: discord.TextChannel,
    sender: discord.Member,
    interaction: discord.Interaction = None
):
    # DB에서 해당 티켓 채널의 손님(user_id) 조회
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT user_id FROM commissions WHERE ticket_channel = ?",
            (channel.id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        msg = "❌ 이 채널의 손님 정보를 DB에서 찾을 수 없습니다."
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await channel.send(msg)
        return

    customer_id = row[0]
    customer = channel.guild.get_member(customer_id)

    if not customer:
        msg = "❌ 서버에서 손님 멤버를 찾을 수 없습니다."
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await channel.send(msg)
        return

    # DM 발송용 알림 임베드
    embed = discord.Embed(
        title="🔔 디자이너 호출 알림",
        description=f"**{channel.guild.name}**의 **{sender.display_name}** 디자이너님이 손님을 찾고 계십니다!\n빠른 진행을 위해 아래 링크를 눌러 채널로 이동해 주세요.",
        color=0x5865F2
    )
    embed.add_field(name="🔗 티켓 채널 바로가기", value=f"[여기 클릭해서 이동하기]({channel.jump_url})")

    # 손님에게 DM 발송
    try:
        await customer.send(embed=embed)
        result_text = f"✅ {customer.mention} 손님께 DM 호출 알림을 성공적으로 보냈습니다!"
    except discord.Forbidden:
        result_text = f"⚠️ {customer.mention} 손님님이 DM을 닫아두셔서 알림을 보내지 못했습니다."

    if interaction and not interaction.response.is_done():
        await interaction.response.send_message(result_text)
    else:
        await channel.send(result_text)


# --------------------------------------------------
# 1. 메인 티켓 오픈 View & 티켓 채널 호출 View
# --------------------------------------------------
class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 티켓 생성",
        style=discord.ButtonStyle.green,
        custom_id="open_ticket_btn"
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


class TicketCallView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔔 손님 호출",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_call_btn"
    )
    async def call_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await handle_customer_call(interaction.channel, interaction.user, interaction)


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

        int_val = int(val)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE commissions SET progress = ? WHERE ticket_channel = ?",
                (int_val, channel.id)
            )
            await db.commit()

        await channel.send(f"📊 **{interaction.user.mention}** 님이 진행률을 **{int_val}%**로 변경했습니다.")
        await interaction.response.send_message(f"✅ 진행률이 **{int_val}%**로 변경되었습니다.", ephemeral=True)


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

    @button(label="🔔 손님 호출", style=discord.ButtonStyle.primary)
    async def call_customer(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)
        await handle_customer_call(channel, interaction.user, interaction)

    @button(label="📊 진행률 설정", style=discord.ButtonStyle.secondary)
    async def set_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProgressModal(self.ticket_channel_id))

    @button(label="💳 계좌 전송", style=discord.ButtonStyle.success)
    async def send_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await channel.send("💳 **입금 계좌 안내**\n`카카오뱅크 3333-XX-XXXXXX (예금주: Dial)`\n입금 후 입금자명을 채널에 남겨주세요!")
        await interaction.response.send_message("✅ 티켓 채널에 계좌 안내를 전송했습니다.", ephemeral=True)

    @button(label="📌 상태 변경", style=discord.ButtonStyle.secondary)
    async def change_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal(self.ticket_channel_id))

    @button(label="✅ 작업 완료", style=discord.ButtonStyle.success)
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

    @button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await channel.send("🔒 **디자이너 요청으로 5초 후 티켓이 종료됩니다.**")
        await interaction.response.send_message("✅ 티켓 종료 안내 메시지를 전송했습니다.", ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE commissions SET status = 'closed' WHERE ticket_channel = ?",
                (channel.id,)
            )
            await db.commit()

        await asyncio.sleep(5)
        try:
            await channel.delete(reason="디자이너 컨트롤 패널에 의한 티켓 종료")
        except discord.NotFound:
            pass
        except discord.Forbidden:
            print(f"[경고] {channel.name} 채널을 삭제할 권한이 부족합니다.")
