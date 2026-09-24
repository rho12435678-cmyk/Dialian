import asyncio
from datetime import datetime, time, timedelta, timezone
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import traceback

import aiosqlite
import discord
from discord import ui
from discord.ext import commands, tasks

from config import *
from database.DailyNotice import DailyNotice
from database.backups import backup_database
from database.database import DATABASE, create_tables
from database.monthly_stats import (
    build_monthly_stats_embed,
    save_monthly_stats_message,
    update_monthly_stats_message,
)
from database.services.points import (
    add_user_points,
    get_user_points,
    process_daily_attendance,
    credit_review_award,
)
from database.services.roblox_verification import MIN_ACCOUNT_AGE_DAYS, MIN_AVATAR_ROBUX
from database.services.ticket_layout import ticket_name, get_or_create_ticket_category, organize_existing_ticket, designer_tier
from database.services.point_ranking import build_point_embed, refresh_point_ranking
from database.services.update_announcement import announce_once, GUILD_ID as DDS_RELEASE_GUILD_ID
from database.views.claim_view import ClaimTicketView
from database.views.close_ticket import (
    TicketCloseView,
    archive_ticket_channel,
    delete_ticket_channel,
    delete_ticket_dm_messages,
    has_designer_role,
)
from database.views.designer_select import DesignerView
from database.views.payment_view import PaymentView
from database.views.review_view import StarRatingView
from database.views.verify_view import RobloxConfirmView, VerifyView

TOKEN = os.getenv("TOKEN")

# ----------------------------------------------------
# 📌 런타임 전용 설정
# 채널/역할/포인트 정책 값은 config.py를 단일 소스로 사용합니다.
# ----------------------------------------------------
ATTENDANCE_REWARD = 10
DAILY_ACTION_LIMIT = 3
MAX_BET = 500  # 포인트 폭주 방지용 최대 배팅 제한

# ----------------------------------------------------
# 🛡️ 통합 보안 설정 및 상태 변수
# ----------------------------------------------------
user_message_tracker = {}     # 도배 감지용 변수
admin_action_tracker = {}      # 대량 행위 추적용 변수
active_minigame_users = set()  # 미니게임 동시 실행 방지용 유저 세트

SPAM_MESSAGE_LIMIT = 5       # 감지 시간 내 허용 메시지 수
SPAM_TIME_WINDOW = 3.0       # 감지 시간 간격 (초)
MAX_MENTION_LIMIT = 6        # 한 메시지 당 최대 허용 멘션 수

MASS_ACTION_WINDOW = 10.0
MASS_CHANNEL_LIMIT = 3
MASS_ROLE_LIMIT = 3
MASS_KICK_LIMIT = 3
MASS_BAN_LIMIT = 3
MASS_WEBHOOK_LIMIT = 2

DM_TRADE_KEYWORDS = [
    "디엠주세요", "디엠 주세요", "dm주세요", "dm 주세요",
    "뒷디", "개인톡", "카톡주세요", "카톡 주세요",
    "싸게 해드림", "개인 커미션", "사적으로", "따로 연락",
    "개인메시지", "개인 메세지", "뒷거래"
]

DANGEROUS_EXTENSIONS = (
    '.exe', '.bat', '.ps1', '.scr', '.vbs', '.cmd', '.jar',
    '.pif', '.application', '.gadget', '.msi', '.msp', '.com', '.hta', '.cpl',
    '.msc', '.vbe', '.jse', '.wsf', '.wsh', '.ps2', '.psc1', '.psc2'
)

DISCORD_TOKEN_REGEX = r"[\w-]{24,28}\.[\w-]{6}\.[\w-]{27,38}"
PHONE_REGEX = r"\b01[016789]-?\d{3,4}-?\d{4}\b"
RRN_REGEX = r"\b\d{6}-[1-8]\d{6}\b"

PROCESSED_TABLES = {
    "processed_commands",
    "processed_command_errors",
}


# ==================== [유틸리티 & 기본 헬퍼] ====================

def get_bot_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def parse_mention_id(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d{17,20})", str(text))
    return int(match.group(1)) if match else None


async def fetch_member_or_none(guild: discord.Guild, member_id: int):
    if not member_id or not guild:
        return None
    member = guild.get_member(member_id)
    if member:
        return member
    try:
        return await guild.fetch_member(member_id)
    except Exception:
        return None


# ==================== [🔒 블랙리스트 헬퍼 함수] ====================

async def init_blacklist_table(db: aiosqlite.Connection):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            created_at TEXT
        )
    """)
    await db.commit()


async def is_blacklisted(db: aiosqlite.Connection, user_id: int) -> bool:
    async with db.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        return row is not None


async def get_blacklist_info(db: aiosqlite.Connection, user_id: int):
    async with db.execute("SELECT reason, created_at FROM blacklist WHERE user_id = ?", (user_id,)) as cursor:
        return await cursor.fetchone()


async def add_blacklist(db: aiosqlite.Connection, user_id: int, reason: str):
    now = discord.utils.utcnow().isoformat()
    await db.execute(
        "INSERT OR REPLACE INTO blacklist (user_id, reason, created_at) VALUES (?, ?, ?)",
        (user_id, reason, now)
    )
    await db.commit()


async def remove_blacklist(db: aiosqlite.Connection, user_id: int):
    await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    await db.commit()


# ==================== [티켓 / 커미션 데이터베이스 헬퍼] ====================

def is_ticket_channel(channel) -> bool:
    return isinstance(channel, discord.TextChannel) and channel.name.startswith("티켓-")


def is_ticket_or_archive_channel(channel) -> bool:
    return isinstance(channel, discord.TextChannel) and (channel.name.startswith("티켓-") or channel.name.startswith("보관-티켓-"))


async def find_ticket_owner(channel: discord.TextChannel):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT customer_id FROM commissions WHERE ticket_channel = ?", (channel.id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                member = await fetch_member_or_none(channel.guild, row[0])
                if member:
                    return member

    try:
        if channel.topic:
            match = re.search(r"손님 ID:\s*(\d+)", channel.topic)
            if not match:
                match = re.search(r"\d+", channel.topic)
            if match:
                return await fetch_member_or_none(channel.guild, int(match.group(1)))
    except (TypeError, ValueError):
        pass

    async for msg in channel.history(limit=5, oldest_first=True):
        if msg.mentions:
            return msg.mentions[0]
    return None


async def find_ticket_designer_id(channel: discord.TextChannel) -> int | None:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT designer_id FROM commissions WHERE ticket_channel = ?", (channel.id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]

    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            for field in embed.fields:
                if field.name == "👨‍💻 담당 디자이너":
                    designer_id = parse_mention_id(field.value)
                    if designer_id:
                        return designer_id
            if embed.description:
                designer_id = parse_mention_id(embed.description)
                if designer_id:
                    return designer_id
    return None


def can_manage_ticket(member: discord.Member, user_id: int, designer_id: int | None) -> bool:
    if member is None:
        return False
    if member.guild_permissions.administrator:
        return True
    if designer_id is not None:
        return user_id == designer_id
    return has_designer_role(member)


async def update_commission_progress(channel: discord.TextChannel, progress: int):
    now_str = discord.utils.utcnow().isoformat()
    status = "completed" if progress == 100 else "in_progress"
    async with aiosqlite.connect(DATABASE) as db:
        if progress == 100:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?, status = ?, completed_at = COALESCE(completed_at, ?), updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now_str, now_str, channel.id)
            )
        else:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?, status = ?, updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now_str, channel.id)
            )
        await db.commit()


async def upsert_commission_record(data: dict):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """
            INSERT INTO commissions(ticket_channel, customer_id, designer_id, category, status, progress, created_at, completed_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket_channel) DO UPDATE SET
                customer_id = excluded.customer_id,
                designer_id = excluded.designer_id,
                category = excluded.category,
                status = excluded.status,
                progress = excluded.progress,
                completed_at = COALESCE(commissions.completed_at, excluded.completed_at),
                updated_at = excluded.updated_at
            """,
            (
                data["ticket_channel"], data["customer_id"], data["designer_id"],
                data["category"], data["status"], data["progress"],
                data["created_at"], data["completed_at"], data["updated_at"],
            )
        )
        await db.commit()


async def send_payment_info(channel: discord.TextChannel, designer_id: int) -> bool:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT bank_name, account_number, holder FROM bank_accounts WHERE developer_id = ?",
            (designer_id,)
        ) as cursor:
            data = await cursor.fetchone()

    if data is None:
        return False

    bank_name, account_number, holder = data
    embed = discord.Embed(
        title="💳 결제 정보",
        description=f"🏦 {bank_name}\n계좌번호 : `{account_number}`\n예금주 : **{holder}**\n\n✅ 입금 후 담당 디자이너에게 말씀해주세요.",
        color=discord.Color.green()
    )
    await channel.send(embed=embed)
    return True


# ==================== [손님 호출 / 진행상황 연동 핵심 함수] ====================

async def handle_customer_call(
    channel: discord.TextChannel,
    sender: discord.Member,
    interaction: discord.Interaction = None
):
    """손님에게 DM 알림 및 채널 직접 멘션 호출을 수행하여 진행 상황을 재확인하도록 돕는 자동 호출 함수"""
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT customer_id, progress, status, category FROM commissions WHERE ticket_channel = ?",
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

    customer_id, progress, status, category = row[0], row[1], row[2], row[3]
    customer = await fetch_member_or_none(channel.guild, customer_id)

    if not customer:
        msg = "❌ 서버에서 손님 멤버를 찾을 수 없습니다."
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await channel.send(msg)
        return

    progress_val = progress if progress is not None else 0
    status_val = status if status else "진행 중"
    category_val = category if category else "문의"
    status_info = f"🏷️ **항목:** `{category_val}` | 📊 **현재 진행률:** `{progress_val}%` | 📌 **상태:** `{status_val}`"

    embed = discord.Embed(
        title="🔔 디자이너 자동 호출 및 진행 상황 안내",
        description=f"**{channel.guild.name}**의 **{sender.display_name}** 디자이너님이 호출하셨습니다!\n아래 링크를 통해 채널로 이동하여 진행 상황을 확인해 주세요.",
        color=0x5865F2
    )
    embed.add_field(name="📋 커미션 정보 및 진행 상황", value=status_info, inline=False)
    embed.add_field(name="🔗 티켓 채널 바로가기", value=f"[여기 클릭해서 이동하기]({channel.jump_url})", inline=False)

    dm_notice = ""
    try:
        await customer.send(embed=embed)
        dm_notice = "\n*(✉️ 손님 DM으로도 알림을 전송했습니다.)*"
    except discord.Forbidden:
        dm_notice = "\n*(⚠️ 손님의 DM이 차단되어 있어 채널 멘션만 수행되었습니다.)*"

    call_message = (
        f"🔔 {customer.mention} 손님! **{sender.display_name}** 디자이너님이 호출하셨습니다.\n"
        f"> {status_info}{dm_notice}"
    )

    if interaction and not interaction.response.is_done():
        await interaction.response.send_message(call_message)
    else:
        await channel.send(call_message)


# ==================== [티켓 지원 / 파트너 모달 및 뷰 & 디자이너 컨트롤] ====================

class TicketCallView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🔔 손님 호출",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_call_btn"
    )
    async def call_button(self, interaction: discord.Interaction, button: ui.Button):
        await handle_customer_call(interaction.channel, interaction.user, interaction)


class ProgressModal(ui.Modal, title="📊 진행률 설정"):
    progress = ui.TextInput(
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
        await update_commission_progress(channel, int_val)
        
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        await channel.send(f"📊 **{interaction.user.mention}** 님이 진행률을 **{int_val}%**로 변경했습니다.")
        await interaction.response.send_message(f"✅ 진행률이 **{int_val}%**로 변경되었습니다.", ephemeral=True)


class StatusModal(ui.Modal, title="📌 커미션 상태 변경"):
    status_text = ui.TextInput(
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
                "UPDATE commissions SET status = ?, updated_at = ? WHERE ticket_channel = ?",
                (status, discord.utils.utcnow().isoformat(), channel.id)
            )
            await db.commit()
            
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        await channel.send(f"📌 **{interaction.user.mention}** 님이 상태를 변경했습니다.\n**상태:** `{status}`")
        await interaction.response.send_message(f"✅ 상태가 `{status}`(으)로 연동되었습니다.", ephemeral=True)


class DesignerDMControlView(ui.View):
    def __init__(self, ticket_channel_id: int):
        super().__init__(timeout=None)
        self.ticket_channel_id = ticket_channel_id

    @ui.button(label="🔔 손님 호출", style=discord.ButtonStyle.primary)
    async def call_customer(self, interaction: discord.Interaction, button: ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)
        await handle_customer_call(channel, interaction.user, interaction)

    @ui.button(label="📊 진행률 설정", style=discord.ButtonStyle.secondary)
    async def set_progress(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ProgressModal(self.ticket_channel_id))

    @ui.button(label="💳 계좌 전송", style=discord.ButtonStyle.success)
    async def send_account(self, interaction: discord.Interaction, button: ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        sent = await send_payment_info(channel, interaction.user.id)
        if sent:
            await interaction.response.send_message("✅ 티켓 채널에 계좌 안내를 전송했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 등록된 계좌 정보가 없습니다. `!계좌등록` 명령어로 먼저 등록해 주세요.", ephemeral=True)

    @ui.button(label="📌 상태 변경", style=discord.ButtonStyle.secondary)
    async def change_status(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(StatusModal(self.ticket_channel_id))

    @ui.button(label="✅ 작업 완료", style=discord.ButtonStyle.success)
    async def complete_job(self, interaction: discord.Interaction, button: ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await update_commission_progress(channel, 100)
        
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        review_embed = discord.Embed(
            title="⭐ 작업이 완료되었습니다!",
            description="모든 작업이 마무리되었습니다.\n아래 버튼을 눌러 담당 디자이너의 만족도를 평가해주세요!",
            color=discord.Color.gold()
        )
        await channel.send(embed=review_embed, view=StarRatingView(interaction.user.id))
        await interaction.response.send_message("✅ 티켓 채널에 작업 완료 및 평점 요청을 전송했습니다.", ephemeral=True)

    @ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        channel = interaction.client.get_channel(self.ticket_channel_id)
        if not channel:
            return await interaction.response.send_message("❌ 티켓 채널을 찾을 수 없습니다.", ephemeral=True)

        await channel.send("🔒 **디자이너 요청으로 5초 후 티켓 종료가 진행됩니다.**")
        await interaction.response.send_message("✅ 티켓 종료 안내 메시지를 전송했습니다.", ephemeral=True)

        await update_commission_progress(channel, 100)
        
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        await asyncio.sleep(5)
        await archive_ticket_channel(channel)


class DevApplyModal(ui.Modal, title="💻 개발자 지원 신청서"):
    dev_field = ui.TextInput(label="지원 분야", placeholder="예: GFX 디자이너, 복장 디자이너, 스크립터 등", required=True)
    portfolio = ui.TextInput(label="경력 및 포트폴리오 링크", style=discord.TextStyle.paragraph, placeholder="포트폴리오 링크나 작업 경력을 적어주세요.", required=True)
    motive = ui.TextInput(label="지원 동기 및 각오", style=discord.TextStyle.paragraph, placeholder="간단한 지원 동기를 작성해주세요.", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        clean_username = re.sub(r'[^a-zA-Z0-9_-]', '', user.name.lower()) or "user"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=ticket_name("개발자 지원", user),
            category=await get_or_create_ticket_category(guild, "개발자 지원"),
            reason=f"{user.display_name} 님의 개발자 지원 티켓",
            overwrites=overwrites,
            topic=f"손님 ID: {user.id} | 카테고리: 개발자 지원 | 담당 디자이너: 미지정"
        )

        embed = discord.Embed(
            title="💻 개발자 지원 신청서가 접수되었습니다.",
            description=f"**신청자:** {user.mention} (`{user.id}`)\n\n관리자가 신청서를 확인한 후 답변을 드릴 예정입니다.",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="📌 지원 분야", value=self.dev_field.value, inline=False)
        embed.add_field(name="🎨 경력 및 포트폴리오", value=self.portfolio.value, inline=False)
        embed.add_field(name="🔥 지원 동기 및 각오", value=self.motive.value, inline=False)

        await channel.send(content=f"{user.mention} 님, 지원서가 성공적으로 생성되었습니다.", embed=embed, view=TicketCloseView())

        now_str = discord.utils.utcnow().isoformat()
        data = {
            "ticket_channel": channel.id,
            "customer_id": user.id,
            "designer_id": None,
            "category": "개발자 지원",
            "status": "in_progress",
            "progress": 0,
            "created_at": now_str,
            "completed_at": None,
            "updated_at": now_str,
        }
        await upsert_commission_record(data)
        
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        try:
            await user.send(f"📩 DDS 개발자 지원 티켓 바로가기: {channel.jump_url}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(f"✅ 지원 티켓이 생성되었습니다! {channel.mention}", ephemeral=True)


class PartnerApplyModal(ui.Modal, title="🤝 파트너 문의 신청서"):
    partner_type = ui.TextInput(
        label="파트너 유형 / 서버(단체)명",
        placeholder="예: [DDS] 서버 커뮤니티",
        required=True
    )
    member_count = ui.TextInput(
        label="서버 총 인원 (0~100,000명)",
        placeholder="예: 150 (파트너십 조건: 100명 이상)",
        required=True,
        min_length=1,
        max_length=15
    )
    invite_link = ui.TextInput(
        label="서버 영구링크",
        placeholder="예: https://discord.gg/yourserver 또는 영구 링크 입력",
        style=discord.TextStyle.short,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        clean_username = re.sub(r'[^a-zA-Z0-9_-]', '', user.name.lower()) or "user"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=ticket_name("파트너 문의", user),
            category=await get_or_create_ticket_category(guild, "파트너 문의"),
            reason=f"{user.display_name} 님의 파트너 문의 티켓",
            overwrites=overwrites,
            topic=f"손님 ID: {user.id} | 카테고리: 파트너 문의 | 담당 디자이너: 미지정"
        )

        embed = discord.Embed(
            title="🤝 파트너 문의가 접수되었습니다.",
            description=f"**문의자:** {user.mention} (`{user.id}`)\n\n담당자가 제안서를 확인한 후 빠르게 답변해 드리겠습니다.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="🏢 대표 단체 / 서버명", value=self.partner_type.value, inline=False)
        embed.add_field(name="👥 서버 총 인원", value=f"{self.member_count.value} 명", inline=False)
        embed.add_field(name="🔗 서버 영구링크", value=self.invite_link.value, inline=False)

        await channel.send(content=f"{user.mention} 님, 파트너 문의 티켓이 생성되었습니다.", embed=embed, view=TicketCloseView())

        now_str = discord.utils.utcnow().isoformat()
        data = {
            "ticket_channel": channel.id,
            "customer_id": user.id,
            "designer_id": None,
            "category": "파트너 문의",
            "status": "in_progress",
            "progress": 0,
            "created_at": now_str,
            "completed_at": None,
            "updated_at": now_str,
        }
        await upsert_commission_record(data)
        
        # 월간 통계 메시지 자동 갱신 연동
        await update_monthly_stats_message(interaction.client)

        try:
            await user.send(f"📩 DDS 파트너 문의 티켓 바로가기: {channel.jump_url}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(f"✅ 파트너 문의 티켓이 생성되었습니다! {channel.mention}", ephemeral=True)


class CategorySelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎨 GFX 커미션", style=discord.ButtonStyle.primary, custom_id="ticket_gfx", row=0)
    async def btn_gfx(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        view = await DesignerView.create(interaction.guild, "gfx")
        embed = discord.Embed(title="🎨 GFX 디자이너 선택", description="원하시는 디자이너를 선택하거나 랜덤 배정을 선택해주세요.", color=0x5865F2)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="👔 Roblox 복장 커미션", style=discord.ButtonStyle.success, custom_id="ticket_uniform", row=0)
    async def btn_uniform(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        view = await DesignerView.create(interaction.guild, "uniform")
        embed = discord.Embed(title="👔 Roblox 복장 디자이너 선택", description="원하시는 디자이너를 선택하거나 랜덤 배정을 선택해주세요.", color=0x5865F2)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.secondary, custom_id="ticket_dev_apply", row=1)
    async def btn_dev_apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(DevApplyModal())

    @ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.danger, custom_id="ticket_partner_apply", row=1)
    async def btn_partner_apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PartnerApplyModal())

    @ui.button(label="📋 일반 / 묶음 가격표", style=discord.ButtonStyle.secondary, custom_id="price_standard", row=2)
    async def btn_price_standard(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📋 DDS 일반 / 묶음 커미션 가격표",
            description="가독성을 높인 공식 가격표입니다. 아래에서 개별 및 묶음 가격을 확인해보세요!",
            color=discord.Color.blue()
        )
        
        gfx_table = (
            "```\n"
            "┌──────────┬──────────┬───────────┬───────────┐\n"
            "│  등  급  │  단  품  │ 2+1 묶음  │ 3+1 묶음  │\n"
            "├──────────┼──────────┼───────────┼───────────┤\n"
            "│ 초급 GFX │ 5,000 원 │ 10,000 원 │ 15,000 원 │\n"
            "│ 중급 GFX │ 6,500 원 │ 13,000 원 │ 19,500 원 │\n"
            "│ 상급 GFX │ 8,500 원 │ 17,000 원 │ 25,500 원 │\n"
            "└──────────┴──────────┴───────────┴───────────┘\n"
            "```"
        )
        embed.add_field(name="🎨 GFX 단품 & 묶음 공식 가격표", value=gfx_table, inline=False)

        uniform_table = (
            "```\n"
            "┌──────────────┬──────────┬───────────┬───────────┐\n"
            "│    구  분    │  단  품  │ 2+1 묶음  │ 3+1 묶음  │\n"
            "├──────────────┼──────────┼───────────┼───────────┤\n"
            "│  상•하 개별  │ 5,000 원 │ 10,000 원 │ 15,000 원 │\n"
            "│  바리에이션  │   500 원 │     -     │     -     │\n"
            "└──────────────┴──────────┴───────────┴───────────┘\n"
            "```"
        )
        embed.add_field(name="👔 Roblox 복장 단품 & 묶음 공식 가격표", value=uniform_table, inline=False)

        bundle_info = (
            "```\n"
            "• GFX / 복장 2+1 묶음 : 2개 가격으로 총 3개 제작! (1개 무료 혜택)\n"
            "• GFX / 복장 3+1 묶음 : 3개 가격으로 총 4개 제작! (1개 무료 혜택)\n"
            "• 복장 바리에이션     : 색상/디자인 변형 추가 시 개당 500원\n"
            "```"
        )
        embed.add_field(name="🎁 묶음 할인 혜택 (Bundle Sale)", value=bundle_info, inline=False)
        embed.set_footer(text="💡 가격 문의 및 특수 주문은 커미션 티켓 생성을 이용해 주세요.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="⭐ 단골 전용 (20% 할인) 가격표", style=discord.ButtonStyle.success, custom_id="price_vip", row=2)
    async def btn_price_vip(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="⭐ DDS 단골 전용 (20% 할인) 가격표",
            description="단골 회원님(1,000P 이상 달성)을 위한 Special 20% 할인 가격표입니다!",
            color=discord.Color.gold()
        )

        vip_gfx_table = (
            "```\n"
            "┌──────────┬──────────┬───────────┬───────────┐\n"
            "│  등  급  │ 20% 단품 │ 2+1 묶음  │ 3+1 묶음  │\n"
            "├──────────┼──────────┼───────────┼───────────┤\n"
            "│ 초급 GFX │ 4,000 원 │  8,000 원 │ 12,000 원 │\n"
            "│ 중급 GFX │ 5,200 원 │ 10,400 원 │ 15,600 원 │\n"
            "│ 상급 GFX │ 6,800 원 │ 13,600 원 │ 20,400 원 │\n"
            "└──────────┴──────────┴───────────┴───────────┘\n"
            "```"
        )
        embed.add_field(name="🎨 GFX 단골 20% 할인 단품 & 묶음가", value=vip_gfx_table, inline=False)

        vip_uniform_table = (
            "```\n"
            "┌──────────────┬──────────┬───────────┬───────────┐\n"
            "│    구  분    │ 20% 단품 │ 2+1 묶음  │ 3+1 묶음  │\n"
            "├──────────────┼──────────┼───────────┼───────────┤\n"
            "│  상•하 개별  │ 4,000 원 │  8,000 원 │ 12,000 원 │\n"
            "│  바리에이션  │   400 원 │     -     │     -     │\n"
            "└──────────────┴──────────┴───────────┴───────────┘\n"
            "```"
        )
        embed.add_field(name="👔 Roblox 복장 단골 20% 할인 단품 & 묶음가", value=vip_uniform_table, inline=False)

        vip_info = (
            "```\n"
            "• 혜택 대상 : 1,000 P 이상 달성 유저 (단골 역할 자동 부여)\n"
            "• 적용 범위 : 단품 및 2+1, 3+1 묶음 결제 시 20% 자동 할인가 적용\n"
            "```"
        )
        embed.add_field(name="👑 단골 혜택 안내", value=vip_info, inline=False)
        embed.set_footer(text="✨ 늘 이용해 주셔서 감사합니다!")

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== [자동 DB 정기 Clean-up 태스크] ====================

@tasks.loop(hours=6)
async def cleanup_processed_records():
    try:
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                DELETE FROM processed_commands 
                WHERE message_id NOT IN (
                    SELECT message_id FROM processed_commands ORDER BY message_id DESC LIMIT 5000
                )
            """)
            await db.execute("""
                DELETE FROM processed_command_errors 
                WHERE message_id NOT IN (
                    SELECT message_id FROM processed_command_errors ORDER BY message_id DESC LIMIT 5000
                )
            """)
            await db.commit()
    except Exception as e:
        print(f"[DB Cleanup Error] {e}")


# ==================== [DB 자동 백업 태스크] ====================

@tasks.loop(hours=24)
async def scheduled_database_backup():
    try:
        backup_path = await backup_database()
        if backup_path:
            print(f"[DB Backup] 백업 완료: {backup_path}")
    except Exception as e:
        print(f"[DB Backup Error] {e}")


# ==================== [월간 통계 자동 갱신 태스크] ====================

@tasks.loop(hours=1)
async def auto_update_monthly_stats():
    try:
        await update_monthly_stats_message(bot)
    except Exception as e:
        print(f"[월간 통계 자동 갱신 오류] {e}")

@auto_update_monthly_stats.before_loop
async def before_auto_update_stats():
    await bot.wait_until_ready()


# One-time, bot-authored release notice. Persistent DB and Discord message footer
# also prevent repeat posts on service restarts or a second manual invocation.
@tasks.loop(count=1)
async def publish_combined_dds_update():
    try:
        await announce_once(bot)
    except Exception as exc:
        print(f"[DDS 일회성 업데이트 공지 실패] {type(exc).__name__}: {exc}")


@publish_combined_dds_update.before_loop
async def before_publish_combined_dds_update():
    await bot.wait_until_ready()


# ==================== [봇 클래스 정의 및 Persistent View / DB 초기화] ====================

class DialianBot(commands.Bot):
    restart_requested = False

    async def setup_hook(self):
        await create_tables()
        await init_extended_db()

        self.add_view(CategorySelectView())
        self.add_view(VerifyView())
        self.add_view(RobloxConfirmView())
        self.add_view(TicketCloseView())
        self.add_view(ClaimTicketView())
        self.add_view(TicketCallView())
        self.add_view(PaymentView())
        self.add_view(StarRatingView())

        await self.add_cog(DailyNotice(self))

        # 외부 포인트 Cog 로드
        try:
            await self.load_extension("database.services.points")
        except Exception as e:
            print(f"[포인트 Cog 로드 실패]: {e}")

        # OpenAI 키가 있을 때만 자동 번역 확장을 활성화합니다.
        if os.getenv("OPENAI_API_KEY"):
            try:
                await self.load_extension("database.services.auto_translator")
            except Exception as e:
                print(f"[자동 번역 Cog 로드 실패]: {e}")
        else:
            print("[Auto Translator] OPENAI_API_KEY 미설정: 자동 번역 비활성화")

        if not cleanup_processed_records.is_running():
            cleanup_processed_records.start()

        if not scheduled_database_backup.is_running():
            scheduled_database_backup.start()

        if not auto_update_monthly_stats.is_running():
            auto_update_monthly_stats.start()

        if not repair_review_awards.is_running():
            repair_review_awards.start()

        if not publish_combined_dds_update.is_running():
            publish_combined_dds_update.start()


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = DialianBot(command_prefix="!", intents=intents, help_command=None)

bot_started_at = discord.utils.utcnow()


# ==================== [🛡️ 보안 & 로그 헬퍼 함수] ====================

async def get_security_channel(guild: discord.Guild):
    if not guild:
        return None
    sec_channel = guild.get_channel(SECURITY_LOG_CHANNEL_ID)
    if not sec_channel:
        try:
            sec_channel = await bot.fetch_channel(SECURITY_LOG_CHANNEL_ID)
        except Exception:
            sec_channel = None
    return sec_channel


async def log_security_event(guild: discord.Guild, title: str, description: str, color=discord.Color.red()):
    if not guild:
        return
    sec_channel = await get_security_channel(guild)
    if sec_channel:
        embed = discord.Embed(
            title=f"🛡️ [보안 경고] {title}",
            description=description,
            color=color,
            timestamp=discord.utils.utcnow()
        )
        try:
            await sec_channel.send(embed=embed)
        except Exception as e:
            print(f"[보안 로그 전송 실패] {e}")


async def check_and_punish_mass_action(guild: discord.Guild, user_id: int, action_type: str, limit: int):
    if not guild or user_id == guild.owner_id:
        return

    now = discord.utils.utcnow()
    tracker_key = f"{user_id}:{action_type}"
    
    for k, ts_list in list(admin_action_tracker.items()):
        valid_ts = [t for t in ts_list if (now - t).total_seconds() < MASS_ACTION_WINDOW * 2]
        if valid_ts:
            admin_action_tracker[k] = valid_ts
        else:
            admin_action_tracker.pop(k, None)

    timestamps = admin_action_tracker.get(tracker_key, [])
    timestamps = [t for t in timestamps if (now - t).total_seconds() < MASS_ACTION_WINDOW]
    timestamps.append(now)
    admin_action_tracker[tracker_key] = timestamps

    if len(timestamps) >= limit:
        member = guild.get_member(user_id)
        if member:
            roles_to_remove = [r for r in member.roles if r != guild.default_role and not r.managed]
            try:
                await member.remove_roles(*roles_to_remove, reason=f"대량 조작 감지 ({action_type})")
            except Exception as e:
                print(f"[역할 박탈 실패] {e}")

            try:
                await member.timeout(discord.utils.utcnow() + timedelta(hours=24), reason=f"보안 위협: 대량 {action_type} 시도")
            except Exception:
                pass

            await log_security_event(
                guild,
                f"대량 {action_type} 감지 - 자동 차단 집행",
                f"**행위자:** {member.mention} (`{member.id}`)\n"
                f"**감지 유형:** `{action_type}` (10초 내 {len(timestamps)}회 시도)\n"
                f"**조치 내용:** 모든 역할 박탈 및 24시간 격리(Timeout)",
                discord.Color.dark_red()
            )


async def claim_once(table_name: str, message_id: int) -> bool:
    if table_name not in PROCESSED_TABLES:
        raise ValueError("허용되지 않은 처리 기록 테이블입니다.")

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("PRAGMA busy_timeout = 5000;")
        cursor = await db.execute(
            f"INSERT OR IGNORE INTO {table_name}(message_id) VALUES (?)" ,
            (message_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


@bot.check
async def prevent_duplicate_command_processing(ctx):
    if ctx.command and ctx.command.name in ["업데이트", "업데이트확인"]:
        return True
    return await claim_once("processed_commands", ctx.message.id)


# ==================== [DB 확장 구조 초기화] ====================

async def init_extended_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await init_blacklist_table(db)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_commands (
                message_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_command_errors (
                message_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS point_ranking_panel (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER,
                message_id INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS designer_tier_panel (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER,
                message_id INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS point_reset_logs (
                year_month TEXT PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS commissions (
                ticket_channel INTEGER PRIMARY KEY,
                customer_id INTEGER,
                designer_id INTEGER,
                category TEXT,
                status TEXT,
                progress INTEGER DEFAULT 0,
                created_at TEXT,
                completed_at TEXT,
                updated_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_activity_limits (
                user_id INTEGER,
                action_type TEXT,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, action_type, date)
            )
        """)
        await db.commit()


# ==================== [디자이너 등급 패널 로직] ====================

async def build_designer_tier_embed(guild: discord.Guild):
    gfx_role_id = DESIGNER_ROLE_IDS["gfx"]
    uniform_role_id = DESIGNER_ROLE_IDS["uniform"]

    gfx_members = [m for m in guild.members if any(r.id == gfx_role_id for r in m.roles)]
    uniform_members = [m for m in guild.members if any(r.id == uniform_role_id for r in m.roles)]

    def classify_gfx_tiers(members):
        high, mid, low = [], [], []
        for m in members:
            role_names = [r.name for r in m.roles]
            if any("상급" in name for name in role_names):
                high.append(m.mention)
            elif any("중급" in name for name in role_names):
                mid.append(m.mention)
            elif any("초급" in name for name in role_names):
                low.append(m.mention)
        return high, mid, low

    gfx_high, gfx_mid, gfx_low = classify_gfx_tiers(gfx_members)

    embed = discord.Embed(
        title="🎨 Dialian 디자이너 등급 현황",
        description="실시간으로 자동 갱신되는 DDS 공식 디자이너 등급 목록입니다. ✨",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow()
    )

    def fmt(lst):
        return ", ".join(lst) if lst else "없음"

    embed.add_field(
        name="🖼️ GFX 디자이너 목록",
        value=(
            f"🥇 **상급 디자이너**: {fmt(gfx_high)}\n"
            f"🥈 **중급 디자이너**: {fmt(gfx_mid)}\n"
            f"🥉 **초급 디자이너**: {fmt(gfx_low)}"
        ),
        inline=False
    )

    uni_mentions = [m.mention for m in uniform_members]
    embed.add_field(
        name="👔 Roblox 복장 디자이너 목록",
        value=f"🧵 **복장 디자이너**: {fmt(uni_mentions)}",
        inline=False
    )

    embed.set_footer(text="자동 동기화 시스템 작동 중 | 멤버 및 역할 변경 시 자동 반영")
    return embed


async def update_designer_tier_panel_message(bot_instance):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT channel_id, message_id FROM designer_tier_panel WHERE id = 1") as cursor:
            row = await cursor.fetchone()

    if not row:
        return

    channel_id, message_id = row
    channel = bot_instance.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
        embed = await build_designer_tier_embed(channel.guild)
        await message.edit(embed=embed)
    except Exception as e:
        print(f"[디자이너 등급 패널 갱신 오류] {e}")


# ==================== [🛡️ 멤버/권한/웹훅 이벤트 & 보안 감지] ====================

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    async with aiosqlite.connect(DATABASE) as db:
        if await is_blacklisted(db, member.id):
            info = await get_blacklist_info(db, member.id)
            reason = info[0] if info else "사유 미기재"
            
            try:
                await member.ban(reason=f"[블랙리스트 자동 차단] 사유: {reason}")
            except Exception as e:
                print(f"[블랙리스트 자동 차단 실패] {e}")

            await log_security_event(
                guild,
                "🚫 블랙리스트 유저 자동 차단 (Ban)",
                f"**차단 대상:** {member.mention} (`{member.id}`)\n"
                f"**등록된 사유:** `{reason}`\n"
                f"**조치 내용:** 서버 재입장 시도 즉시 영구 차단 집행",
                discord.Color.dark_red()
            )
            return

    if member.bot:
        try:
            await asyncio.sleep(1.0)
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if entry.target.id == member.id and entry.user.id != guild.owner_id:
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                        try:
                            await member.kick(reason="승인되지 않은 봇 무단 추가")
                        except Exception:
                            pass
                        await log_security_event(
                            guild,
                            "🤖 승인되지 않은 봇 차단",
                            f"**초대된 봇:** {member.mention} (`{member.id}`)\n**초대한 유저:** <@{entry.user.id}>",
                            discord.Color.red()
                        )
                        await check_and_punish_mass_action(guild, entry.user.id, "무단 봇 초대", 1)
                        return
        except discord.Forbidden:
            pass


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        await update_designer_tier_panel_message(bot)
        if designer_tier(before, "GFX") != designer_tier(after, "GFX"):
            # Keep the grade in existing GFX ticket names accurate after role changes.
            async with aiosqlite.connect(DATABASE) as db:
                async with db.execute("""
                    SELECT ticket_channel, customer_id, category FROM commissions
                    WHERE designer_id=? AND LOWER(category) LIKE '%gfx%'
                      AND status NOT IN ('closed', 'completed', 'cancelled')
                    ORDER BY created_at DESC LIMIT 100
                """, (after.id,)) as cursor:
                    tickets = await cursor.fetchall()
            for channel_id, customer_id, category in tickets:
                channel = after.guild.get_channel(channel_id)
                if channel:
                    try:
                        customer = after.guild.get_member(customer_id)
                        await organize_existing_ticket(channel, category, customer, after)
                    except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                        print(f"[GFX 등급 변경에 따른 티켓명 갱신 실패] {exc}")


@bot.event
async def on_member_remove(member: discord.Member):
    await update_designer_tier_panel_message(bot)
    
    guild = member.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "Kick(추방)", MASS_KICK_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.target.id == user.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "Ban(차단)", MASS_BAN_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_webhooks_update(channel: discord.TextChannel):
    guild = channel.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_create):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "웹훅 생성", MASS_WEBHOOK_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    if after.is_default():
        dangerous_perms = ['administrator', 'manage_roles', 'manage_channels', 'kick_members', 'ban_members', 'mention_everyone']
        
        has_violation = False
        for perm in dangerous_perms:
            before_val = getattr(before.permissions, perm, False)
            after_val = getattr(after.permissions, perm, False)
            if not before_val and after_val:
                has_violation = True
                break
                
        if has_violation:
            try:
                await after.edit(permissions=before.permissions, reason="Permission Guard: @everyone 위험 권한 자동 박탈")
            except Exception:
                pass
            
            try:
                await asyncio.sleep(1.0)
                async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_update):
                    if entry.target.id == after.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                        await log_security_event(
                            after.guild,
                            "🛡️ [Permission Guard] 위험 권한 자동 회수",
                            f"**수행자:** <@{entry.user.id}>\n**내용:** `@everyone` 역할에 위험 권한 추가 감지 ➔ 권한 원복 집행",
                            discord.Color.dark_red()
                        )
                        break
            except discord.Forbidden:
                pass


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = channel.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
            if entry.target.id == channel.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "채널 삭제", MASS_CHANNEL_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    guild = channel.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_create):
            if entry.target.id == channel.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                # Legitimate DDS auto-created categories/tickets must not trigger
                # anti-raid sanctions against the bot itself.
                if bot.user and entry.user.id == bot.user.id:
                    break
                await check_and_punish_mass_action(guild, entry.user.id, "채널 생성", MASS_CHANNEL_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.role_delete):
            if entry.target.id == role.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "역할 삭제", MASS_ROLE_LIMIT)
                break
    except discord.Forbidden:
        pass


@bot.event
async def on_guild_role_create(role: discord.Role):
    guild = role.guild
    try:
        await asyncio.sleep(1.0)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.role_create):
            if entry.target.id == role.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                await check_and_punish_mass_action(guild, entry.user.id, "역할 생성", MASS_ROLE_LIMIT)
                break
    except discord.Forbidden:
        pass


# ==================== [포인트 랭킹 패널 헬퍼] ====================

async def build_point_ranking_embed(guild: discord.Guild):
    return await build_point_embed(guild)

async def update_point_ranking_message(bot_instance):
    try:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute("SELECT channel_id, message_id FROM point_ranking_panel WHERE id = 1") as cursor:
                row = await cursor.fetchone()

        if not row:
            return

        channel_id, message_id = row
        channel = bot_instance.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot_instance.fetch_channel(channel_id)
            except Exception:
                return

        message = await channel.fetch_message(message_id)
        embed = await build_point_ranking_embed(channel.guild)
        await message.edit(embed=embed)
    except Exception as e:
        print(f"[랭킹 패널 갱신 오류] {e}")


# ==================== [후기 포인트 보류 자동 복구 및 랭킹 재동기화] ====================

@tasks.loop(minutes=3)
async def repair_review_awards():
    guild = None
    ranking_channel = bot.get_channel(POINT_RANKING_CHANNEL_ID)
    if ranking_channel:
        guild = ranking_channel.guild
    if not guild and len(bot.guilds) == 1:
        guild = bot.guilds[0]
    if not guild:
        return

    # Resolve publication-unknown reviews without accidentally rewarding a
    # review that never appeared in the public channel.
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT ticket_channel FROM review_point_awards
            WHERE status='unpublished' ORDER BY created_at LIMIT 30
        """) as cursor:
            unknown_ids = {record[0] for record in await cursor.fetchall()}
    if unknown_ids:
        review_channel = guild.get_channel(REVIEWS_CHANNEL_ID)
        if review_channel is None:
            review_channel = discord.utils.get(guild.text_channels, name=REVIEW_CHANNEL_NAME)
        published = {}
        if review_channel:
            try:
                async for message in review_channel.history(limit=1000):
                    if message.author.id != bot.user.id:
                        continue
                    for embed in message.embeds:
                        if embed.title != "✨ 소중한 커미션 후기가 도착했습니다!":
                            continue
                        footer = embed.footer.text if embed.footer else ""
                        match = re.search(r"Ticket ID:\s*(\d+)", footer or "")
                        if match and int(match.group(1)) in unknown_ids:
                            published[int(match.group(1))] = message.id
                    if len(published) == len(unknown_ids):
                        break
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[후기 게시 확인 실패] {exc}")
        if published:
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute("BEGIN IMMEDIATE")
                for ticket_id, message_id in published.items():
                    await db.execute(
                        """UPDATE review_point_awards
                           SET status='pending', review_message_id=?
                           WHERE ticket_channel=? AND status='unpublished'""",
                        (message_id, ticket_id),
                    )
                await db.commit()

    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT ticket_channel, customer_id FROM review_point_awards
            WHERE status='pending' ORDER BY created_at LIMIT 30
        """) as cursor:
            pending = await cursor.fetchall()

    for ticket_id, customer_id in pending:
        try:
            member = guild.get_member(customer_id) or discord.Object(id=customer_id)
            await credit_review_award(guild, member, ticket_id)
        except Exception as exc:
            print(f"[후기 포인트 자동 복구 실패] ticket={ticket_id} {exc}")

    await refresh_point_ranking(guild)


@repair_review_awards.before_loop
async def before_repair_review_awards():
    await bot.wait_until_ready()


# ==================== [채널 유효성 및 일일 제한 헬퍼] ====================

async def check_command_channel(ctx) -> bool:
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.send(f"❌ 해당 명령어는 <#{COMMAND_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.", delete_after=5)
        return False
    return True


async def check_and_increment_daily_limit(user_id: int, action_type: str, max_limit: int = DAILY_ACTION_LIMIT):
    today = discord.utils.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT count FROM daily_activity_limits
            WHERE user_id = ? AND action_type = ? AND date = ?
        """, (user_id, action_type, today)) as cursor:
            row = await cursor.fetchone()
            current_count = row[0] if row else 0

        if current_count >= max_limit:
            return False, current_count

        await db.execute("""
            INSERT INTO daily_activity_limits (user_id, action_type, date, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, action_type, date) DO UPDATE SET count = count + 1
        """, (user_id, action_type, today))
        await db.commit()
        return True, current_count + 1


# ==================== [메시지 이벤트 & 보안 검사] ====================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    author = message.author
    guild = message.guild
    sec_channel = await get_security_channel(guild)

    try:
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith(DANGEROUS_EXTENSIONS):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    
                    if sec_channel:
                        embed = discord.Embed(
                            title="🚨 [보안 경고] 위험 실행 파일 업로드 감지",
                            description=f"**유저:** {author.mention} (`{author.id}`)\n**파일명:** `{attachment.filename}`\n**발생 채널:** {message.channel.mention}",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow()
                        )
                        await sec_channel.send(embed=embed)
                    return

        clean_content = re.sub(r"<@!?\d+>|<@&\d+>|<#\d+>", "", message.content)
        if (
            message.channel.id not in EXCLUDED_PII_CHANNELS
            and (
                re.search(DISCORD_TOKEN_REGEX, clean_content)
                or re.search(RRN_REGEX, clean_content)
                or re.search(PHONE_REGEX, clean_content)
            )
        ):
            try:
                await message.delete()
            except Exception:
                pass
            
            if sec_channel:
                embed = discord.Embed(
                    title="🔒 [보안 경고] 민감 정보 유출 차단 (PII Guard)",
                    description=f"**유저:** {author.mention} (`{author.id}`)\n**발생 채널:** {message.channel.mention}\n**조치:** 토큰/개인정보 유출 위험 메시지 즉시 삭제",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow()
                )
                await sec_channel.send(embed=embed)
            return

        is_staff = (
            author.guild_permissions.administrator
            or has_designer_role(author)
            or any(role.name in ["관리자", "Staff", "디자이너"] for role in author.roles)
        )
        if not is_staff and author.id != guild.owner_id:
            
            msg_content = message.content.replace(" ", "").lower()
            found_keyword = [word for word in DM_TRADE_KEYWORDS if word.replace(" ", "") in msg_content]

            if found_keyword:
                try:
                    await message.delete()
                except Exception:
                    pass

                if sec_channel:
                    embed = discord.Embed(
                        title="🕵️‍♂️ [보안 경고] 뒷매 의심 키워드 감지",
                        description=f"**감지된 유저:** {author.mention} (`{author.id}`)\n"
                                    f"**적발 키워드:** `{found_keyword[0]}`\n"
                                    f"**원본 메시지:** {message.content}\n"
                                    f"**발생 채널:** {message.channel.mention}",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    await sec_channel.send(embed=embed)
                return

            total_mentions = len(message.mentions) + len(message.role_mentions)
            if message.mention_everyone or total_mentions >= MAX_MENTION_LIMIT:
                try:
                    await message.delete()
                except Exception:
                    pass

                if sec_channel:
                    embed = discord.Embed(
                        title="🚨 [보안 경고] 대량 멘션 시도 감지",
                        description=f"**유저:** {author.mention} (`{author.id}`)\n**발생 채널:** {message.channel.mention}\n**멘션 수:** {total_mentions}회",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    await sec_channel.send(embed=embed)
                return

            now = discord.utils.utcnow()
            for k, ts_list in list(user_message_tracker.items()):
                valid_ts = [t for t in ts_list if (now - t).total_seconds() < SPAM_TIME_WINDOW]
                if valid_ts:
                    user_message_tracker[k] = valid_ts
                else:
                    user_message_tracker.pop(k, None)

            timestamps = user_message_tracker.get(author.id, [])
            timestamps.append(now)
            user_message_tracker[author.id] = timestamps

            if len(timestamps) >= SPAM_MESSAGE_LIMIT:
                try:
                    await message.delete()
                except Exception:
                    pass

                if sec_channel:
                    embed = discord.Embed(
                        title="⚠️ [보안 경고] 도배 행위 감지",
                        description=f"**유저:** {author.mention} (`{author.id}`)\n**발생 채널:** {message.channel.mention}\n**조치:** 도배 메시지 삭제",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    await sec_channel.send(embed=embed)
                return

    except Exception as e:
        print(f"[on_message 보안 및 이벤트 처리 중 예외 발생] {e}")
        traceback.print_exc()

    await bot.process_commands(message)


# ==================== [전역 예외 처리 핸들러] ====================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ 해당 명령어를 실행할 권한이 없습니다.", delete_after=5)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("서버 안에서만 사용할 수 있는 명령어입니다.", delete_after=5)
    elif isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ 필수 인자가 누락되었습니다: `{error.param.name}`", delete_after=5)
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ 명령어 쿨다운 중입니다. {error.retry_after:.1f}초 후 다시 시도해주세요.", delete_after=5)
    else:
        print(f"[Command Error in {ctx.command}]: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)


# ==================== [명령어 모음] ====================

@bot.command(name="인증패널")
@commands.guild_only()
@commands.has_permissions(administrator=True)
@commands.bot_has_permissions(send_messages=True, embed_links=True)
async def verification_panel(ctx):
    embed = discord.Embed(
        title="로블록스 인증",
        description=(
            "로블록스 계정을 인증하면 서버 닉네임이 로블록스 사용자이름으로 바뀌고 손님 역할이 지급됩니다.\n\n"
            f"인증 조건: 계정 생성 후 **{MIN_ACCOUNT_AGE_DAYS}일 이상**, "
            f"착용 아이템의 현재 판매가 합계 **{MIN_AVATAR_ROBUX}로벅 이상**\n\n"
            "기존 회원은 **인증 정보 업데이트**를 눌러주세요.\n"
            "아래 버튼에서 시작해주세요. 비밀번호나 쿠키는 필요하지 않습니다."
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed, view=VerifyView())


@bot.command(name="인증업데이트")
@commands.guild_only()
@commands.cooldown(1, 30, commands.BucketType.member)
@commands.bot_has_permissions(send_messages=True, embed_links=True)
async def verification_update(ctx):
    embed = discord.Embed(
        title="로블록스 인증 정보 업데이트",
        description=(
            "아래 **인증 정보 업데이트**를 누르면 연결된 로블록스 계정의 인증 조건을 다시 검사하고 "
            "닉네임과 손님 역할을 갱신합니다.\n"
            "아직 계정을 연결하지 않았다면 **로블록스 인증하기**를 눌러주세요.\n\n"
            f"조건: 계정 생성 후 {MIN_ACCOUNT_AGE_DAYS}일 이상 · "
            f"현재 착용 아이템 판매가 합계 {MIN_AVATAR_ROBUX}로벅 이상"
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed, view=VerifyView(), delete_after=180)


@bot.command(name="명령어", aliases=["help", "도움말"])
async def command_list(ctx):
    embed = discord.Embed(
        title="Dialian 명령어 목록",
        description=(
            "**[티켓 및 일반 서비스]**\n"
            "`!티켓생성` `!계좌전송 [@유저]` `!티켓닫기` `!티켓삭제` `!인증패널` `!인증업데이트` `!담당 @유저` `!호출`\n"
            "`!진행 0|25|50|75|100` `!예상 [시간]` `!완료` `!티켓정보` `!고객` `!소유자변경 @유저` `!청소 1~100`\n"
            "`!계좌등록 @유저 은행 계좌번호 예금주` `!계좌목록` `!계좌삭제 @유저`\n"
            "`!통계` `!진행티켓` `!강제종료`\n\n"
            "**[🔒 블랙리스트 관리]**\n"
            "`!블랙 @유저/ID [사유]` (DB 등록 및 즉시 영구 차단)\n"
            "`!블랙해제 @유저/ID` (DB 삭제 및 서버 차단 해제)\n"
            "`!블랙조회 @유저/ID` (블랙리스트 여부 및 사유 조회)\n\n"
            "**[패널 및 가이드 설정]**\n"
            "`!포인트안내` (포인트 적립 채널에 공지 임베드 전송)\n"
            "`!디자이너등급패널` (디자이너 등급 실시간 패널 생성)\n"
            "`!포인트랭킹` (포인트 실시간 TOP 10 패널 생성)\n\n"
            "**[시스템 & 관리]**\n"
            "`!재시작` (관리자 전용 봇 재시작)\n"
            "`!업데이트확인` `!업데이트` (최신 Git Pull 반영)\n\n"
            "**[포인트 & 프로필]** *(명령어 채널 전용)*\n"
            "`!출석체크` (매일 1회 출석 체크 시 **+10P** 지급!)\n"
            "`!포인트` `!포인트지급 @유저 금액` `!포인트차감 @유저 금액` `!포인트리셋 @유저`\n\n"
            "**[🎰 오락실 & 미니게임]** *(명령어 채널 전용 / 최대 배팅: 500P)*\n"
            "`!뽑기` - 20P 소모 (최대 150P 획득 가능)\n"
            "`!가위바위보 [가위/바위/보] [배팅포인트]` - 승리 시 배팅액의 1.95배 지급!\n"
            "`!묵찌빠 [가위/바위/보] [배팅포인트]` - 정식 심리전 대결 (승리 시 2.0배 지급!)"
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


# ==================== [🔒 블랙리스트 명령어] ====================

@bot.command(name="블랙", aliases=["블랙등록", "차단등록"])
@commands.has_permissions(administrator=True)
async def add_to_blacklist(ctx, user_input: str, *, reason: str = "사유 미기재"):
    user_id = parse_mention_id(user_input)
    if not user_id:
        return await ctx.send("❌ 유효한 유저 Mention 또는 ID를 입력해 주세요.")

    async with aiosqlite.connect(DATABASE) as db:
        await add_blacklist(db, user_id, reason)

    member = ctx.guild.get_member(user_id)
    if member:
        try:
            await member.ban(reason=f"[관리자 등록 블랙리스트] {reason}")
        except Exception as e:
            await ctx.send(f"⚠️ DB 등록 완료되었으나, 서버 차단 실패: `{e}`")

    await ctx.send(f"✅ **ID: `{user_id}`** 님이 블랙리스트에 등록되었습니다. (사유: {reason})")
    await log_security_event(
        ctx.guild,
        "🚫 블랙리스트 유저 수동 등록",
        f"**처리자:** {ctx.author.mention}\n**대상 유저 ID:** `{user_id}`\n**사유:** {reason}",
        discord.Color.red()
    )


@bot.command(name="블랙해제", aliases=["차단해제"])
@commands.has_permissions(administrator=True)
async def remove_from_blacklist(ctx, user_input: str):
    user_id = parse_mention_id(user_input)
    if not user_id:
        return await ctx.send("❌ 유효한 유저 Mention 또는 ID를 입력해 주세요.")

    async with aiosqlite.connect(DATABASE) as db:
        await remove_blacklist(db, user_id)

    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason="[관리자 요청] 블랙리스트 해제")
    except Exception:
        pass

    await ctx.send(f"✅ **ID: `{user_id}`** 님의 블랙리스트 등록 및 차단이 해제되었습니다.")
    await log_security_event(
        ctx.guild,
        "🟢 블랙리스트 해제",
        f"**처리자:** {ctx.author.mention}\n**대상 유저 ID:** `{user_id}`",
        discord.Color.green()
    )


@bot.command(name="블랙조회", aliases=["블랙확인"])
@commands.has_permissions(administrator=True)
async def check_blacklist(ctx, user_input: str):
    user_id = parse_mention_id(user_input)
    if not user_id:
        return await ctx.send("❌ 유효한 유저 Mention 또는 ID를 입력해 주세요.")

    async with aiosqlite.connect(DATABASE) as db:
        info = await get_blacklist_info(db, user_id)

    if info:
        reason, created_at = info
        embed = discord.Embed(
            title="🚫 블랙리스트 유저 정보",
            color=discord.Color.red()
        )
        embed.add_field(name="유저 ID", value=f"`{user_id}`", inline=False)
        embed.add_field(name="등록 사유", value=f"`{reason}`", inline=False)
        embed.add_field(name="등록 일시", value=f"`{created_at}`", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"ℹ️ **ID: `{user_id}`** 님은 블랙리스트에 등록되어 있지 않습니다.")


@bot.command(name="디자이너등급패널")
@commands.has_permissions(administrator=True)
async def setup_designer_tier_panel(ctx):
    if ctx.channel.id != DESIGNER_TIER_CHANNEL_ID:
        return await ctx.send(f"❌ <#{DESIGNER_TIER_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.", delete_after=5)

    embed = await build_designer_tier_embed(ctx.guild)
    msg = await ctx.send(embed=embed)

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO designer_tier_panel (id, channel_id, message_id)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
        """, (ctx.channel.id, msg.id))
        await db.commit()

    try:
        await ctx.message.delete()
    except Exception:
        pass


# ==================== [🎰 보안 강화 미니게임 섹션] ====================

@bot.command(name="뽑기", aliases=["가챠", "럭키드로우"])
@commands.cooldown(1, 2, commands.BucketType.user)
async def point_gacha(ctx):
    if not await check_command_channel(ctx):
        return

    if ctx.author.id in active_minigame_users:
        return await ctx.send("⏳ 이미 미니게임이 진행 중입니다. 잠시 후 다시 시도해주세요!", delete_after=3)

    active_minigame_users.add(ctx.author.id)
    try:
        current_points = await get_user_points(ctx.author.id)
        cost = GACHA_COST

        if current_points < cost:
            return await ctx.send(f"❌ 포인트가 부족합니다. (현재 `{current_points:,}P` / 필요 `{cost}P`)")

        await add_user_points(ctx.guild, ctx.author, -cost)

        prizes = [0, 10, 50, 150]
        weights = [60, 25, 10, 5]
        result = random.choices(prizes, weights=weights, k=1)[0]

        if result > 0:
            await add_user_points(ctx.guild, ctx.author, result)

        final_points = await get_user_points(ctx.author.id)
        await update_point_ranking_message(bot)

        if result == 0:
            color, title, desc = discord.Color.dark_grey(), "😭 아쉬운 꽝!", "포인트를 얻지 못했습니다."
        elif result == 10:
            color, title, desc = discord.Color.light_grey(), "💧 소액 환급!", "소모한 포인트의 절반인 **10P**를 돌려받았습니다."
        elif result == 50:
            color, title, desc = discord.Color.gold(), "🎉 축하합니다! 당첨!", f"**+{result}P**를 얻으셨습니다!"
        elif result == 150:
            color, title, desc = discord.Color.magenta(), "🔥 150P 잭팟 터짐!!!", f"극악의 확률을 뚫고 무려 **{result}P**를 획득했습니다!"

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="현재 잔여 포인트", value=f"`{final_points:,} P`", inline=False)
        await ctx.send(embed=embed)
    finally:
        active_minigame_users.discard(ctx.author.id)


@bot.command(name="가위바위보")
@commands.cooldown(1, 2, commands.BucketType.user)
async def rock_paper_scissors(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!가위바위보 [가위/바위/보] [배팅포인트]`")

    if bet < 10 or bet > MAX_BET:
        return await ctx.send(f"❌ 배팅 금액은 최소 `10 P` 이상, 최대 `{MAX_BET:,} P` 이하이어야 합니다.")

    if ctx.author.id in active_minigame_users:
        return await ctx.send("⏳ 이미 미니게임이 진행 중입니다. 잠시 후 다시 시도해주세요!", delete_after=3)

    active_minigame_users.add(ctx.author.id)
    try:
        current_points = await get_user_points(ctx.author.id)
        if current_points < bet:
            return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points:,}P`)")

        bot_choice = random.choice(choices)

        if choice == bot_choice:
            result = "draw"
        elif (choice == "가위" and bot_choice == "보") or \
             (choice == "바위" and bot_choice == "가위") or \
             (choice == "보" and bot_choice == "바위"):
            result = "win"
        else:
            result = "lose"

        if result == "win":
            win_profit = int(bet * 0.95)
            await add_user_points(ctx.guild, ctx.author, win_profit)
            final_points = await get_user_points(ctx.author.id)
            embed = discord.Embed(
                title="✌️🖐️✊ 가위바위보 승리!",
                description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n🎉 승리하여 **+{win_profit:,}P** (배팅액의 1.95배)를 획득했습니다!",
                color=discord.Color.green()
            )
        elif result == "draw":
            final_points = current_points
            embed = discord.Embed(
                title="✌️🖐️✊ 가위바위보 무승부!",
                description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n비겼으므로 배팅한 포인트를 그대로 돌려받습니다.",
                color=discord.Color.light_grey()
            )
        else:
            await add_user_points(ctx.guild, ctx.author, -bet)
            final_points = await get_user_points(ctx.author.id)
            embed = discord.Embed(
                title="✌️🖐️✊ 가위바위보 패배...",
                description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n😭 패배하여 `{bet:,}P`를 잃었습니다.",
                color=discord.Color.red()
            )

        await update_point_ranking_message(bot)
        embed.add_field(name="현재 보유 포인트", value=f"`{final_points:,} P`", inline=False)
        await ctx.send(embed=embed)
    finally:
        active_minigame_users.discard(ctx.author.id)


@bot.command(name="묵찌빠")
@commands.cooldown(1, 2, commands.BucketType.user)
async def muk_jji_bba(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!묵찌빠 [가위/바위/보] [배팅포인트]`")

    if bet < 20 or bet > MAX_BET:
        return await ctx.send(f"❌ 묵찌빠 배팅 금액은 최소 `20 P` 이상, 최대 `{MAX_BET:,} P` 이하이어야 합니다.")

    if ctx.author.id in active_minigame_users:
        return await ctx.send("⏳ 이미 미니게임이 진행 중입니다. 잠시 후 다시 시도해주세요!", delete_after=3)

    active_minigame_users.add(ctx.author.id)
    try:
        current_points = await get_user_points(ctx.author.id)
        if current_points < bet:
            return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points:,}P`)")

        def get_rps_winner(p1_move, p2_move):
            if p1_move == p2_move:
                return "draw"
            if (p1_move == "가위" and p2_move == "보") or \
               (p1_move == "바위" and p2_move == "가위") or \
               (p1_move == "보" and p2_move == "바위"):
                return "p1"
            return "p2"

        bot_choice1 = random.choice(choices)
        first_round_result = get_rps_winner(choice, bot_choice1)

        if first_round_result == "draw":
            embed = discord.Embed(
                title="👊✌️🖐️ 묵찌빠 - 무승부",
                description=f"유저: **{choice}** vs 봇: **{bot_choice1}**\n\n첫 판 가위바위보에서 비겼으므로 승패 없이 판돈을 돌려받습니다.",
                color=discord.Color.light_grey()
            )
            embed.add_field(name="현재 보유 포인트", value=f"`{current_points:,} P`", inline=False)
            return await ctx.send(embed=embed)

        attacker = "user" if first_round_result == "p1" else "bot"
        logs = [f"**[1턴 - 선공 결정]** 유저({choice}) vs 봇({bot_choice1}) ➔ **{'유저' if attacker == 'user' else '봇'}** 선공!"]

        winner = None
        turn = 2

        while turn <= 6:
            u_move = choice if turn == 2 else random.choice(choices)
            b_move = random.choice(choices)
            att_name = "유저" if attacker == "user" else "봇"

            if u_move == b_move:
                winner = attacker
                logs.append(f"**[{turn}턴 - 최종]** {att_name} 공격! 유저({u_move}) vs 봇({b_move}) ➔ **{att_name} 승리!** 🎉")
                break
            else:
                turn_result = get_rps_winner(u_move, b_move)
                new_attacker = "user" if turn_result == "p1" else "bot"
                new_att_name = "유저" if new_attacker == "user" else "봇"
                logs.append(f"**[{turn}턴]** {att_name} 공격 실패! 유저({u_move}) vs 봇({b_move}) ➔ 공격권 **{new_att_name}**에게 이동!")
                attacker = new_attacker
                turn += 1

        if not winner:
            logs.append("**[종료]** 6턴 넘게 치열한 접전이 이어져 무승부 처리되었습니다.")

        if winner == "user":
            win_profit = bet 
            await add_user_points(ctx.guild, ctx.author, win_profit)
            final_points = await get_user_points(ctx.author.id)
            embed = discord.Embed(
                title="👊✌️🖐️ 묵찌빠 승리!",
                description="\n".join(logs) + f"\n\n🎉 치열한 대결 끝에 승리하여 **+{win_profit:,}P**를 획득했습니다!",
                color=discord.Color.gold()
            )
        elif winner == "bot":
            await add_user_points(ctx.guild, ctx.author, -bet)
            final_points = await get_user_points(ctx.author.id)
            embed = discord.Embed(
                title="👊✌️🖐️ 묵찌빠 패배...",
                description="\n".join(logs) + f"\n\n😭 패배하여 `{bet:,}P`를 잃었습니다.",
                color=discord.Color.red()
            )
        else:
            final_points = current_points
            embed = discord.Embed(
                title="👊✌️🖐️ 묵찌빠 무승부!",
                description="\n".join(logs),
                color=discord.Color.light_grey()
            )

        await update_point_ranking_message(bot)
        embed.add_field(name="현재 보유 포인트", value=f"`{final_points:,} P`", inline=False)
        await ctx.send(embed=embed)
    finally:
        active_minigame_users.discard(ctx.author.id)


# ==================== [포인트 관리 전용 명령어] ====================

@bot.command(name="포인트지급")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 5, commands.BucketType.user)
async def give_points(ctx, member: discord.Member, amount: int):
    if amount > 50000 and ctx.author.id != ctx.guild.owner_id:
        return await ctx.send("❌ 1회에 최대 50,000 P 까지만 지급할 수 있습니다.")

    new_points = await add_user_points(ctx.guild, member, amount)
    await update_point_ranking_message(bot)
    await ctx.send(f"✅ {member.mention} 님에게 `{amount:,} P`를 지급했습니다. (현재: `{new_points:,} P`)")
    await log_security_event(ctx.guild, "포인트 강제 지급", f"수행자: {ctx.author.mention}\n대상: {member.mention}\n지급액: `{amount:,}P`", discord.Color.blue())


@bot.command(name="포인트차감")
@commands.has_permissions(administrator=True)
async def remove_points(ctx, member: discord.Member, amount: int):
    new_points = await add_user_points(ctx.guild, member, -amount)
    await update_point_ranking_message(bot)
    await ctx.send(f"✅ {member.mention} 님의 포인트를 `{amount:,} P` 차감했습니다. (현재: `{new_points:,} P`)")
    await log_security_event(ctx.guild, "포인트 강제 차감", f"수행자: {ctx.author.mention}\n대상: {member.mention}\n차감액: `{amount:,}P`", discord.Color.orange())


@bot.command(name="포인트리셋")
@commands.has_permissions(administrator=True)
async def reset_points(ctx, member: discord.Member):
    current_points = await get_user_points(member.id)
    if current_points > 0:
        await add_user_points(ctx.guild, member, -current_points)
    await update_point_ranking_message(bot)
    await ctx.send(f"🔄 {member.mention} 님의 포인트를 `0 P`로 초기화했습니다.")
    await log_security_event(ctx.guild, "포인트 리셋", f"수행자: {ctx.author.mention}\n대상: {member.mention}\n리셋 전 포인트: `{current_points:,}P`", discord.Color.red())


@bot.command(name="랭킹갱신")
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def force_refresh_ranking(ctx):
    refreshed = await refresh_point_ranking(ctx.guild)
    await ctx.send("✅ 포인트 랭킹을 갱신했습니다." if refreshed else
                   "⚠️ 랭킹 패널을 찾지 못했습니다. 랭킹 채널에서 !포인트랭킹을 먼저 실행해주세요.")


@bot.command(name="후기포인트점검")
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def audit_september_reviews(ctx):
    """Report September 2026 legacy records without guessing prior rewards."""
    if ctx.channel.id != SECURITY_LOG_CHANNEL_ID:
        return await ctx.send(
            f"🔒 보안실 <#{SECURITY_LOG_CHANNEL_ID}>에서만 점검할 수 있습니다.",
            delete_after=10,
        )
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT r.ticket_channel, r.customer_id, r.stars,
                   COALESCE(a.status, 'unknown'), COALESCE(a.amount, 0)
            FROM reviews r LEFT JOIN review_point_awards a
              ON a.ticket_channel=r.ticket_channel
            WHERE substr(r.created_at, 1, 7)='2026-09'
            ORDER BY r.created_at DESC
        """) as cursor:
            records = await cursor.fetchall()
    unverified = [row for row in records if row[3] in ("legacy_unverified", "unknown")]
    pending = [row for row in records if row[3] == "pending"]
    credited = [row for row in records if row[3] == "awarded"]
    # Historical embeds did not include the ticket ID. Compare aggregate counts
    # to flag potential DB/public-channel discrepancies, never guess a payout.
    published_count = None
    review_channel = ctx.guild.get_channel(REVIEWS_CHANNEL_ID)
    if review_channel is not None:
        try:
            september_start = datetime(2026, 8, 31, 15, tzinfo=timezone.utc)
            october_start = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
            published_count = 0
            async for message in review_channel.history(
                limit=1500, after=september_start, before=october_start
            ):
                if message.author.id == bot.user.id and any(
                    embed.title == "✨ 소중한 커미션 후기가 도착했습니다!"
                    for embed in message.embeds
                ):
                    published_count += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[9월 후기 채널 조회 실패] {exc}")
    channel_summary = (
        f" / 후기 채널 게시물: **{published_count}건**"
        if published_count is not None else " / 후기 채널은 조회할 수 없음"
    )
    embed = discord.Embed(
        title="🪙 2026년 9월 후기 포인트 점검",
        description=(
            f"DB 등록: **{len(records)}건**{channel_summary} / 신규 지급 기록: **{len(credited)}건**\n"
            f"자동 재시도 대기: **{len(pending)}건** / "
            f"과거 지급 여부 미확인: **{len(unverified)}건**\n\n"
            "**주의:** 기존 시스템에는 후기별 포인트 지급 내역이 없어서 "
            "과거 후기를 일괄 재지급하면 중복 적립될 수 있습니다. "
            "실제 지급 누락을 확인한 건만 관리자가 복구해주세요."
        ),
        color=discord.Color.gold(),
    )
    if unverified:
        embed.add_field(
            name="확인 필요 (최근 최대 15건)",
            value="\n".join(
                f"티켓 `{ticket_id}` · <@{user_id}> · {stars}점"
                for ticket_id, user_id, stars, _, _ in unverified[:15]
            )[:1024],
            inline=False,
        )
    # Earlier builds mistakenly used database/database.db for points. Detect
    # that file read-only; differences are diagnostic, NOT unpaid rewards.
    old_path = Path("database/database.db")
    if old_path.is_file():
        try:
            async with aiosqlite.connect(old_path.resolve().as_uri() + "?mode=ro",
                                         uri=True) as legacy:
                async with legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='user_points'"
                ) as cursor:
                    exists = await cursor.fetchone()
                if exists:
                    async with legacy.execute(
                        "SELECT COUNT(*) FROM user_points"
                    ) as cursor:
                        old_users = (await cursor.fetchone())[0]
                    embed.add_field(
                        name="⚠️ 구버전 포인트 DB 감지",
                        value=(
                            f"과거 `database/database.db` 파일에 **{old_users}명**의 "
                            "포인트 기록이 남아 있습니다. 현재 DB로 이미 이전되었는지는 "
                            "확인되지 않았습니다. 이 값을 자동 합산하지 마세요."
                        ),
                        inline=False,
                    )
        except (OSError, aiosqlite.Error) as exc:
            print(f"[구버전 포인트 DB 읽기 실패] {exc}")

    embed.add_field(
        name="복구 방법",
        value="후기 채널 게시물 및 과거 포인트 내역을 먼저 확인한 뒤 "
              "`!후기포인트복구 티켓ID 단품` 또는 "
              "`!후기포인트복구 티켓ID 2+1` / "
              "`!후기포인트복구 티켓ID 3+1`",
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.command(name="후기포인트복구")
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def restore_september_review(ctx, ticket_id: int, bundle: str):
    if ctx.channel.id != SECURITY_LOG_CHANNEL_ID:
        return await ctx.send(
            f"🔒 보안실 <#{SECURITY_LOG_CHANNEL_ID}>에서만 복구할 수 있습니다.",
            delete_after=10,
        )
    amounts = {
        "단품": REVIEW_POINTS_SINGLE,
        "2+1": REVIEW_POINTS_2_PLUS_1,
        "3+1": REVIEW_POINTS_3_PLUS_1,
    }
    if bundle not in amounts:
        return await ctx.send("묶음 종류는 `단품`, `2+1`, `3+1` 중 하나여야 합니다.")
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT r.customer_id, a.status
            FROM reviews r JOIN review_point_awards a
              ON r.ticket_channel=a.ticket_channel
            WHERE r.ticket_channel=? AND substr(r.created_at,1,7)='2026-09'
        """, (ticket_id,)) as cursor:
            row = await cursor.fetchone()
    if not row or row[1] != "legacy_unverified":
        return await ctx.send(
            "복구 가능한 9월 과거 후기 기록이 없거나 이미 처리된 티켓입니다."
        )
    member = ctx.guild.get_member(row[0]) or discord.Object(id=row[0])
    try:
        amount, total, changed = await credit_review_award(
            ctx.guild, member, ticket_id,
            administrator_id=ctx.author.id, legacy_amount=amounts[bundle],
        )
    except ValueError as exc:
        return await ctx.send(f"⚠️ {exc}")
    if not changed:
        return await ctx.send("이미 지급된 후기입니다. 중복 적립하지 않았습니다.")
    await ctx.send(
        f"✅ 티켓 `{ticket_id}`의 후기 지급을 수동 승인했습니다. "
        f"<@{row[0]}> +{amount}P · 현재 {total}P"
    )
    await log_security_event(
        ctx.guild, "9월 후기 포인트 개별 복구",
        f"관리자: {ctx.author.mention}\n티켓: {ticket_id}\n"
        f"대상: <@{row[0]}>\n확인한 구성: {bundle}\n적립: {amount}P",
        discord.Color.gold(),
    )


@bot.command(name="티켓정리")
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def organize_active_tickets(ctx):
    """Move only live, DB-backed tickets; never expose or move archived tickets."""
    progress = await ctx.send("🔄 기존 진행 티켓을 분야와 담당자별로 정리하고 있습니다.")
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT ticket_channel, customer_id, designer_id, category
            FROM commissions
            WHERE status NOT IN ('completed', 'cancelled', 'closed')
              AND ticket_channel IS NOT NULL
            ORDER BY created_at DESC LIMIT 200
        """) as cursor:
            records = await cursor.fetchall()
    moved, unchanged, failed = 0, 0, 0
    for channel_id, customer_id, designer_id, category in records:
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue
        customer = ctx.guild.get_member(customer_id)
        designer = ctx.guild.get_member(designer_id) if designer_id else None
        try:
            old_name, old_category = channel.name, channel.category_id
            await organize_existing_ticket(channel, category, customer, designer)
            if channel.name != old_name or channel.category_id != old_category:
                moved += 1
            else:
                unchanged += 1
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            failed += 1
            print(f"[기존 티켓 정리 실패] channel={channel_id}: {exc}")
    await progress.edit(
        content=f"✅ 진행 티켓 정리 완료: 정리 {moved}건 · 유지 {unchanged}건 · 오류 {failed}건."
    )


@bot.command(name="포인트랭킹", aliases=["랭킹패널", "주간베스트", "명예의전당"])
@commands.has_permissions(administrator=True)
async def setup_point_ranking(ctx):
    if ctx.channel.id != POINT_RANKING_CHANNEL_ID:
        return await ctx.send(f"❌ 이 명령어는 <#{POINT_RANKING_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.", delete_after=5)

    embed = await build_point_ranking_embed(ctx.guild)
    msg = await ctx.send(embed=embed)

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO point_ranking_panel (id, channel_id, message_id)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
        """, (ctx.channel.id, msg.id))
        await db.commit()

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="업데이트확인", aliases=["봇상태"])
@commands.has_permissions(administrator=True)
async def update_check(ctx):
    embed = discord.Embed(title="봇 실행 정보", color=discord.Color.green(), timestamp=bot_started_at)
    embed.add_field(name="버전 (Commit)", value=f"`{get_bot_version()}`", inline=True)
    embed.add_field(name="시작 시간", value=discord.utils.format_dt(bot_started_at, style="F"), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="재시작", aliases=["봇재시작", "restart"])
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def restart_bot(ctx):
    if bot.restart_requested:
        return await ctx.send("이미 재시작을 처리하고 있습니다.", delete_after=5)

    bot.restart_requested = True
    try:
        await ctx.send(
            "봇을 재시작합니다. 잠시 연결이 끊어집니다.\n"
            "다시 접속하면 `!봇상태`로 시작 시간을 확인할 수 있습니다."
        )
    except Exception:
        bot.restart_requested = False
        raise
    await bot.close()


@bot.command(name="업데이트", aliases=["패치", "update"])
@commands.has_permissions(administrator=True)
async def update_bot(ctx):
    status_msg = await ctx.send("🔄 **최신 업데이트를 확인하고 반영 중입니다 (git pull)...**")
    bot_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull",
            cwd=bot_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0:
            current_version = get_bot_version()
            embed = discord.Embed(
                title="✅ 업데이트 성공",
                description="git pull 처리가 성공적으로 진행되었습니다.",
                color=discord.Color.blue()
            )
            embed.add_field(name="현재 버전 (Git Commit)", value=f"`{current_version}`", inline=False)

            output_log = stdout_str if stdout_str else "출력 결과 없음"
            if len(output_log) > 1000:
                output_log = output_log[:1000] + "\n... (생략됨)"

            embed.add_field(name="Git 실행 로그", value=f"```\n{output_log}\n```", inline=False)
            embed.set_footer(text="코드 변경사항을 적용하려면 관리자가 !재시작을 입력해주세요.")

            await status_msg.edit(content=None, embed=embed)
        else:
            embed = discord.Embed(
                title="❌ 업데이트 실패 (Git Pull Error)",
                description="Git 실행 도중 오류가 발생했습니다.",
                color=discord.Color.red()
            )
            error_log = stderr_str if stderr_str else stdout_str
            if len(error_log) > 1000:
                error_log = error_log[:1000] + "\n... (생략됨)"

            embed.add_field(name="오류 로그", value=f"```\n{error_log}\n```", inline=False)
            await status_msg.edit(content=None, embed=embed)

    except FileNotFoundError:
        await status_msg.edit(content="❌ **Git이 설치되어 있지 않거나 경로 환경변수가 설정되지 않았습니다.**")
    except Exception as e:
        await status_msg.edit(content=f"❌ **업데이트 중 오류 발생:** `{e}`")


# Failed first attempts may be retried without permitting a second post.
@bot.command(name="업데이트공지1회", aliases=["업뎃공지1회"])
@commands.guild_only()
@commands.has_permissions(administrator=True)
async def manually_publish_combined_update(ctx):
    if ctx.guild.id != DDS_RELEASE_GUILD_ID:
        return await ctx.send("❌ DDS 공식 서버에서만 사용할 수 있습니다.")
    try:
        posted = await announce_once(bot)
    except Exception as exc:
        print(f"[DDS 업데이트 공지 수동 재시도 실패] {type(exc).__name__}: {exc}")
        return await ctx.send(
            "⚠️ 공지를 게시하지 못했습니다. Dialian의 대상 채널 접근, "
            "메시지 전송 및 이전 메시지 읽기 권한을 확인해주세요."
        )
    await ctx.send(
        "✅ 공식 업데이트 채널에 Dialian 명의로 공지를 게시했습니다."
        if posted else
        "ℹ️ 이번 업데이트 공지는 이미 게시되었습니다. 중복 게시하지 않았습니다."
    )


# ==================== [티켓 패널 생성 및 업무 명령어] ====================

@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):
    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description=(
            "상담, 구매 진행, 문의사항이 있으시다면\n"
            "아래 원하시는 항목의 버튼을 클릭해주세요!\n\n"
            "📌 **구매 전 아래 가격표 버튼을 눌러 상세 가격을 확인하실 수 있습니다.**"
        ),
        color=0x5865F2
    )
    embed.set_footer(text="DDS 커미션 시스템")

    await ctx.send(embed=embed, view=CategorySelectView())


@bot.command(name="호출", aliases=["손님호출", "고객호출"])
async def call_customer_command(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    designer_id = await find_ticket_designer_id(ctx.channel)
    if not can_manage_ticket(ctx.author, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 손님을 호출할 수 있습니다.")

    await handle_customer_call(ctx.channel, ctx.author)


@bot.command(name="통계")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    try:
        # 월간 통계 임베드 생성
        embed = await build_monthly_stats_embed(ctx.guild)
        
        # 지정 채널에 메시지 발송
        message = await ctx.send(embed=embed)
        
        # DB에 해당 메시지 정보 저장 (자동 갱신용)
        await save_monthly_stats_message(message)
        
        # 성공 메시지 출력
        await ctx.reply("✅ 월간 통계 패널을 정상적으로 생성 및 등록했습니다.", mention_author=False, delete_after=5)
    except Exception as e:
        # 예외 발생 시 디스코드 채널 및 터미널 콘솔에 상세 에러 출력
        await ctx.send(f"❌ **통계 패널 생성 중 오류 발생:** `{e}`")
        print(f"[통계 명령어 실행 오류] {e}")
        traceback.print_exc()


@bot.command(name="진행티켓", aliases=["진행목록", "티켓목록"])
async def list_active_tickets(ctx):
    member = ctx.guild.get_member(ctx.author.id) if ctx.guild else None
    is_admin = bool(member and member.guild_permissions.administrator)

    if not member or (not is_admin and not has_designer_role(member)):
        return await ctx.send("❌ 관리자 또는 디자이너만 사용할 수 있습니다.")

    async with aiosqlite.connect(DATABASE) as db:
        query = "SELECT ticket_channel, customer_id, designer_id, category, progress, updated_at FROM commissions WHERE status NOT IN ('completed', 'cancelled')"
        params = []
        if not is_admin:
            query += " AND designer_id = ?"
            params.append(ctx.author.id)
        query += " ORDER BY updated_at DESC LIMIT 25"
        
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return await ctx.send("📭 진행 중인 티켓이 없습니다.")

    lines = []
    for ticket_id, customer_id, designer_id, category, progress, updated_at in rows:
        channel = ctx.guild.get_channel(ticket_id)
        channel_text = channel.mention if channel else f"삭제됨 (`{ticket_id}`)"
        customer_text = f"<@{customer_id}>" if customer_id else "알 수 없음"
        designer_text = f"<@{designer_id}>" if designer_id else "미배정"
        lines.append(f"• {channel_text} | {category} | {progress or 0}%\n  고객: {customer_text} / 담당: {designer_text} / ID: `{ticket_id}`")

    embed = discord.Embed(title=f"📋 진행 중 티켓 ({len(rows)}개)", description="\n".join(lines), color=discord.Color.blurple())
    await ctx.send(embed=embed)


@bot.command(name="계좌등록")
@commands.has_permissions(administrator=True)
async def register_bank(ctx, member: discord.Member, bank_name: str, account_number: str, holder: str):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bank_accounts(developer_id, bank_name, account_number, holder) VALUES(?,?,?,?)",
            (member.id, bank_name, account_number, holder)
        )
        await db.commit()
    await ctx.send(f"✅ {member.mention} 님의 계좌가 등록되었습니다.")
    await log_security_event(ctx.guild, "계좌 등록", f"수행자: {ctx.author.mention}\n대상: {member.mention}\n계좌: `{bank_name} {account_number}`", discord.Color.green())


@bot.command(name="계좌목록", aliases=["계좌리스트"])
@commands.has_permissions(administrator=True)
async def list_bank_accounts(ctx):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT developer_id, bank_name, account_number, holder FROM bank_accounts") as cursor:
            rows = await cursor.fetchall()

    if not rows:
        return await ctx.send("📭 등록된 계좌 정보가 없습니다.")

    lines = []
    for dev_id, bank, account, holder in rows:
        lines.append(f"• <@{dev_id}> : **{bank}** `{account}` (예금주: {holder})")

    embed = discord.Embed(title="💳 등록된 디자이너 계좌 목록", description="\n".join(lines), color=discord.Color.blue())
    await ctx.send(embed=embed)


@bot.command(name="계좌삭제")
@commands.has_permissions(administrator=True)
async def delete_bank_account(ctx, member: discord.Member):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("DELETE FROM bank_accounts WHERE developer_id = ?", (member.id,))
        await db.commit()
        deleted = cursor.rowcount > 0

    if deleted:
        await ctx.send(f"✅ {member.mention} 님의 계좌 정보가 삭제되었습니다.")
        await log_security_event(ctx.guild, "계좌 삭제", f"수행자: {ctx.author.mention}\n대상: {member.mention}", discord.Color.red())
    else:
        await ctx.send(f"❌ {member.mention} 님의 등록된 계좌 정보를 찾을 수 없습니다.")


@bot.command(name="계좌전송", aliases=["계좌번호", "결제정보", "결제"])
async def send_bank_to_ticket(ctx, member: discord.Member = None):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    author = ctx.guild.get_member(ctx.author.id) if ctx.guild else None
    is_admin = author and author.guild_permissions.administrator
    is_staff_designer = author and has_designer_role(author)

    # 타 디자이너 계좌를 대리 전송(인자 지정)하거나 기본 담당 디자이너 조회
    target_designer_id = member.id if member else await find_ticket_designer_id(ctx.channel)

    if target_designer_id is None and is_staff_designer:
        target_designer_id = ctx.author.id

    if target_designer_id is None:
        return await ctx.send("❌ 전송할 대상 디자이너 정보나 티켓 담당 디자이너를 찾지 못했습니다.")

    # 권한 검사: 관리자 또는 디자이너 역할을 가진 경우 다른 디자이너의 계좌도 대리 전송 가능
    if not (is_admin or is_staff_designer or ctx.author.id == target_designer_id):
        return await ctx.send("❌ 담당 디자이너, 디자이너 역할 보유자 또는 관리자만 계좌를 전송할 수 있습니다.")

    if not await send_payment_info(ctx.channel, target_designer_id):
        target_name = member.mention if member else "해당 디자이너"
        return await ctx.send(f"❌ {target_name} 님의 계좌가 등록되어 있지 않습니다. `!계좌등록` 명령어로 먼저 등록해 주세요.")

    try:
        await ctx.message.delete()
    except Exception:
        pass

    await ctx.send("✅ 결제 정보를 티켓에 성공적으로 전송했습니다.")


@bot.command(name="담당변경", aliases=["담당", "담당자"])
@commands.has_permissions(administrator=True)
async def change_designer(ctx, designer: discord.Member):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT customer_id, designer_id, category FROM commissions WHERE ticket_channel=?",
            (ctx.channel.id,),
        ) as cursor:
            previous = await cursor.fetchone()
    if not previous:
        return await ctx.send("이 티켓의 DB 정보를 찾을 수 없습니다.")

    customer_id, previous_designer_id, category = previous
    # Permissions first; the DB must not claim a designer is assigned if
    # Discord refused to grant that designer access to the private channel.
    try:
        await ctx.channel.set_permissions(
            designer, view_channel=True, read_messages=True,
            read_message_history=True, send_messages=True, attach_files=True,
        )
    except (discord.Forbidden, discord.HTTPException):
        return await ctx.send("❌ 디자이너에게 티켓 접근 권한을 부여하지 못했습니다.")

    try:
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """UPDATE commissions SET designer_id=?, designer_name=?,
                   updated_at=? WHERE ticket_channel=?""",
                (designer.id, designer.display_name,
                 discord.utils.utcnow().isoformat(), ctx.channel.id),
            )
            await db.commit()
    except Exception:
        if previous_designer_id != designer.id:
            try:
                await ctx.channel.set_permissions(designer, overwrite=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
        raise

    # Do not leave a former designer with explicit access to the new owner's
    # private ticket, unless they also have administrator permissions.
    if previous_designer_id and previous_designer_id != designer.id:
        former = ctx.guild.get_member(previous_designer_id)
        if former and not former.guild_permissions.administrator:
            try:
                await ctx.channel.set_permissions(former, overwrite=None)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[이전 담당자 권한 제거 실패] {exc}")

    warning = ""
    try:
        customer = ctx.guild.get_member(customer_id)
        await organize_existing_ticket(ctx.channel, category, customer, designer)
    except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
        warning = " (채널 정리는 실패했으므로 !티켓정리로 다시 시도해주세요.)"
        print(f"[수동 배정 티켓명 갱신 실패] {exc}")
    await ctx.send(
        f"✅ 티켓 담당 디자이너가 {designer.mention} 님으로 변경되었습니다.{warning}"
    )


@bot.command(name="티켓닫기", aliases=["티켓종료", "닫기"])
async def close_ticket_by_command(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    closer = guild.get_member(ctx.author.id) if guild else None

    if not can_manage_ticket(closer, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 티켓을 종료할 수 있습니다.")

    notice = await ctx.send("🔒 티켓 종료 처리 중입니다.")
    designer = await fetch_member_or_none(guild, designer_id)

    if designer:
        await delete_ticket_dm_messages(bot.user, designer, channel)

    await update_commission_progress(channel, 100)
    
    # 월간 통계 메시지 자동 갱신 연동
    await update_monthly_stats_message(bot)

    await notice.edit(content="✅ 티켓 종료 처리 완료. 곧 보관함으로 이동합니다.")
    await asyncio.sleep(5)
    await archive_ticket_channel(channel)


@bot.command(name="티켓삭제", aliases=["티켓제거", "삭제"])
async def delete_ticket_by_command(ctx):
    if not is_ticket_or_archive_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    deleter = guild.get_member(ctx.author.id) if guild else None

    if not can_manage_ticket(deleter, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 티켓을 삭제할 수 있습니다.")

    await ctx.send("🗑️ 티켓을 삭제합니다.")
    await asyncio.sleep(3)
    await delete_ticket_channel(channel, ctx.author)


# ==================== [진행 및 추가 커스텀 명령어] ====================

@bot.command(name="진행")
async def progress(ctx, percent: int):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    if percent not in [0, 25, 50, 75, 100]:
        return await ctx.send("사용법: `!진행 0|25|50|75|100`")

    designer_id = await find_ticket_designer_id(ctx.channel)
    if not can_manage_ticket(ctx.author, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 진행률을 수정할 수 있습니다.")

    status_labels = {0: "🟢 상담/대기중", 25: "🟡 작업 시작", 50: "🟠 작업 진행중", 75: "🔵 마무리 작업", 100: "✅ 완료"}
    label = status_labels[percent]

    await update_commission_progress(ctx.channel, percent)
    
    # 월간 통계 메시지 자동 갱신 연동
    await update_monthly_stats_message(bot)

    embed = discord.Embed(
        title="📊 커미션 진행 상황 업데이트",
        description=f"📌 **상태:** {label}\n📈 **진행률:** `{percent}%`",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)


@bot.command(name="예상", aliases=["예상시간", "eta"])
async def expected_time(ctx, *, time_str: str):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    designer_id = await find_ticket_designer_id(ctx.channel)
    if not can_manage_ticket(ctx.author, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 예상 시간을 설정할 수 있습니다.")

    embed = discord.Embed(
        title="⏰ 예상 작업 완료 시간 안내",
        description=f"담당 디자이너가 안내하는 예상 완료 일정: **{time_str}**",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)


@bot.command(name="완료")
async def complete(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    designer_id = await find_ticket_designer_id(ctx.channel)
    if not can_manage_ticket(ctx.author, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 완료 처리할 수 있습니다.")

    await update_commission_progress(ctx.channel, 100)
    
    # 월간 통계 메시지 자동 갱신 연동
    await update_monthly_stats_message(bot)

    review_embed = discord.Embed(
        title="⭐ 작업이 완료되었습니다!",
        description="모든 작업이 마무리되었습니다.\n아래 버튼을 눌러 담당 디자이너의 만족도를 평가해주세요!",
        color=discord.Color.gold()
    )
    await ctx.send(embed=review_embed, view=StarRatingView(designer_id))


@bot.command(name="티켓정보", aliases=["티켓상태", "커미션정보"])
async def ticket_info(ctx):
    if not is_ticket_or_archive_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT customer_id, designer_id, category, status, progress, created_at FROM commissions WHERE ticket_channel = ?",
            (ctx.channel.id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return await ctx.send("❌ 해당 티켓의 DB 정보가 존재하지 않습니다.")

    customer_id, designer_id, category, status, progress, created_at = row
    customer = await fetch_member_or_none(ctx.guild, customer_id) if customer_id else None
    designer = await fetch_member_or_none(ctx.guild, designer_id) if designer_id else None

    embed = discord.Embed(title=f"ℹ️ 티켓 정보 - #{ctx.channel.name}", color=discord.Color.blurple())
    embed.add_field(name="👤 주문 고객", value=customer.mention if customer else f"`{customer_id}`", inline=True)
    embed.add_field(name="👨‍💻 담당 디자이너", value=designer.mention if designer else "미배정", inline=True)
    embed.add_field(name="📁 카테고리", value=category or "일반", inline=True)
    embed.add_field(name="📊 진행률", value=f"`{progress}%` ({status})", inline=True)
    embed.add_field(name="📅 생성 일시", value=created_at[:16].replace("T", " ") if created_at else "알 수 없음", inline=True)

    await ctx.send(embed=embed)


@bot.command(name="고객", aliases=["손님", "주문자"])
async def show_ticket_customer(ctx):
    if not is_ticket_or_archive_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    customer = await find_ticket_owner(ctx.channel)
    if customer:
        await ctx.send(f"👤 이 티켓의 주문 고객님은 {customer.mention} (`{customer.id}`) 님입니다.")
    else:
        await ctx.send("❌ 티켓 주문 고객 정보를 확인할 수 없습니다.")


# ==================== [채널 청소 명령어] ====================
@bot.command(name="청소", aliases=["clear", "purge"])
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx, amount: int):
    if amount < 1 or amount > 100:
        return await ctx.send("❌ 1에서 100 사이의 숫자를 입력해주세요.", delete_after=3)
    
    deleted = await ctx.channel.purge(limit=amount + 1)
    
    msg = await ctx.send(f"🧹 **{ctx.author.display_name}**님이 {len(deleted)-1}개의 메시지를 삭제했습니다.")
    await asyncio.sleep(3)
    await msg.delete()


# ==================== [봇 메인 실행부] ====================

def run_bot():
    if TOKEN:
        bot.run(TOKEN)
        # Restart only after asyncio.run has closed connections and cancelled background tasks.
        if bot.restart_requested:
            sys.stdout.flush()
            sys.stderr.flush()
            os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
    else:
        print("❌ 오류: 환경변수에 Discord TOKEN이 설정되지 않았습니다.")


if __name__ == "__main__":
    run_bot()
