import asyncio
import os
import random
import re
import subprocess
import traceback
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import ui
from discord.ext import commands, tasks

from config import *
from database.backups import backup_database
from database.database import DATABASE, create_tables
from database.DailyNotice import DailyNotice
from database.monthly_stats import (
    build_monthly_stats_embed,
    save_monthly_stats_message,
    update_monthly_stats_message,
)
from database.services.points import (
    add_user_points,
    check_and_add_feedback_points,
    check_and_add_share_points,
    get_user_points,
)
from database.views.claim_view import ClaimTicketView
from database.views.close_ticket import (
    TicketCloseView,
    archive_ticket_channel,
    delete_all_bot_dm_messages,
    delete_ticket_channel,
    delete_ticket_dm_messages,
    has_designer_role,
)
from database.views.designer_select import DesignerSelect, DesignerView
from database.views.payment_view import PaymentView
from database.views.review_view import StarRatingView
from database.views.verify_view import VerifyView

TOKEN = os.getenv("TOKEN")

# 핵심 채널 및 역할 ID
POINT_RANKING_CHANNEL_ID = 1532599012316938321
POINT_INFO_CHANNEL_ID = 1532373833783316610
DESIGNER_TIER_CHANNEL_ID = 1537806140711239760

KR_CHAT_CHANNEL_ID = 1505074223356317771
EN_CHAT_CHANNEL_ID = 1527725232864100362

DESIGNER_ROLE_IDS = {
    "gfx": 1518906536095776868,
    "uniform": 1522539025691312168,
}


def get_bot_version():
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


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

daily_notice = None
update_notice_sent = False
bot_started_at = datetime.now()

PROCESSED_TABLES = {
    "processed_commands",
    "processed_command_errors",
}


async def claim_once(table_name, message_id):
    if table_name not in PROCESSED_TABLES:
        raise ValueError("허용되지 않은 처리 기록 테이블입니다.")

    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            f"INSERT OR IGNORE INTO {table_name}(message_id) VALUES (?)",
            (message_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


@bot.check
async def prevent_duplicate_command_processing(ctx):
    if ctx.command and ctx.command.name in ["업데이트", "업데이트확인"]:
        return True
    return await claim_once("processed_commands", ctx.message.id)


# ==================== [DB 초기화] ====================

async def init_extended_db():
    async with aiosqlite.connect(DATABASE) as db:
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
        await db.commit()


# ==================== [티켓 지원 / 파트너 모달 및 뷰] ====================

class DevApplyModal(ui.Modal, title="💻 개발자 지원 신청서"):
    dev_field = ui.TextInput(label="지원 분야", placeholder="예: GFX 디자이너, 복장 디자이너, 스크립터 등", required=True)
    portfolio = ui.TextInput(label="경력 및 포트폴리오 링크", style=discord.TextStyle.paragraph, placeholder="포트폴리오 링크나 작업 경력을 적어주세요.", required=True)
    motive = ui.TextInput(label="지원 동기 및 각오", style=discord.TextStyle.paragraph, placeholder="간단한 지원 동기를 작성해주세요.", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=f"티켓-지원-{user.name}",
            reason=f"{user.display_name} 님의 개발자 지원 티켓",
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="💻 개발자 지원 신청서가 접수되었습니다.",
            description=f"**신청자:** {user.mention} (`{user.id}`)\n\n관리자가 신청서를 확인한 후 답변을 드릴 예정입니다.",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📌 지원 분야", value=self.dev_field.value, inline=False)
        embed.add_field(name="🎨 경력 및 포트폴리오", value=self.portfolio.value, inline=False)
        embed.add_field(name="🔥 지원 동기 및 각오", value=self.motive.value, inline=False)

        await channel.send(content=f"{user.mention} 님, 지원서가 성공적으로 생성되었습니다.", embed=embed, view=TicketCloseView())

        data = {
            "ticket_channel": channel.id,
            "customer_id": user.id,
            "designer_id": None,
            "category": "개발자 지원",
            "status": "in_progress",
            "progress": 0,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "updated_at": datetime.now().isoformat(),
        }
        await upsert_commission_record(data)

        await interaction.followup.send(f"✅ 지원 티켓이 생성되었습니다! {channel.mention}", ephemeral=True)


class PartnerApplyModal(ui.Modal, title="🤝 파트너 문의 신청서"):
    partner_type = ui.TextInput(label="파트너 유형 / 서버(단체)명", placeholder="예: [DDS] 서버 커뮤니티", required=True)
    proposal = ui.TextInput(label="제휴 및 제안 내용", style=discord.TextStyle.paragraph, placeholder="상세 제안 내용을 입력해주세요.", required=True)
    contact = ui.TextInput(label="기타 문의사항", style=discord.TextStyle.paragraph, placeholder="추가 문의사항이 있다면 입력해주세요.", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=f"티켓-파트너-{user.name}",
            reason=f"{user.display_name} 님의 파트너 문의 티켓",
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="🤝 파트너 문의가 접수되었습니다.",
            description=f"**문의자:** {user.mention} (`{user.id}`)\n\n담당자가 제안서를 확인한 후 빠르게 답변해 드리겠습니다.",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        embed.add_field(name="🏢 대표 단체 / 서버명", value=self.partner_type.value, inline=False)
        embed.add_field(name="📝 제휴 제안 내용", value=self.proposal.value, inline=False)
        if self.contact.value:
            embed.add_field(name="💬 기타 문의사항", value=self.contact.value, inline=False)

        await channel.send(content=f"{user.mention} 님, 파트너 문의 티켓이 생성되었습니다.", embed=embed, view=TicketCloseView())

        data = {
            "ticket_channel": channel.id,
            "customer_id": user.id,
            "designer_id": None,
            "category": "파트너 문의",
            "status": "in_progress",
            "progress": 0,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "updated_at": datetime.now().isoformat(),
        }
        await upsert_commission_record(data)

        await interaction.followup.send(f"✅ 파트너 문의 티켓이 생성되었습니다! {channel.mention}", ephemeral=True)


class CategorySelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎨 GFX 커미션", style=discord.ButtonStyle.primary, custom_id="ticket_gfx")
    async def btn_gfx(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        view = await DesignerView.create(interaction.guild, "gfx")
        embed = discord.Embed(title="🎨 GFX 디자이너 선택", description="원하시는 디자이너를 선택하거나 랜덤 배정을 선택해주세요.", color=0x5865F2)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="👔 Roblox 복장 커미션", style=discord.ButtonStyle.success, custom_id="ticket_uniform")
    async def btn_uniform(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        view = await DesignerView.create(interaction.guild, "uniform")
        embed = discord.Embed(title="👔 Roblox 복장 디자이너 선택", description="원하시는 디자이너를 선택하거나 랜덤 배정을 선택해주세요.", color=0x5865F2)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="💻 개발자 지원", style=discord.ButtonStyle.secondary, custom_id="ticket_dev_apply")
    async def btn_dev_apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(DevApplyModal())

    @ui.button(label="🤝 파트너 문의", style=discord.ButtonStyle.danger, custom_id="ticket_partner_apply")
    async def btn_partner_apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PartnerApplyModal())


class CombinedTicketOpenView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="📩 티켓 문의하기", style=discord.ButtonStyle.primary, custom_id="open_ticket_main_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="🎯 원하시는 문의 유형을 선택해주세요",
            description="아래 버튼 중 진행하고 싶으신 종류를 클릭해주세요.",
            color=discord.Color.blurple()
        )
        await interaction.followup.send(embed=embed, view=CategorySelectView(), ephemeral=True)


# ==================== [디자이너 등급 패널 로직] ====================

async def build_designer_tier_embed(guild):
    gfx_role_id = DESIGNER_ROLE_IDS["gfx"]
    uniform_role_id = DESIGNER_ROLE_IDS["uniform"]

    gfx_members = [m for m in guild.members if any(r.id == gfx_role_id for r in m.roles)]
    uniform_members = [m for m in guild.members if any(r.id == uniform_role_id for r in m.roles)]

    # GFX 전용 등급 분류 함수
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
        timestamp=datetime.now()
    )

    def fmt(lst):
        return ", ".join(lst) if lst else "없음"

    # 1. GFX 디자이너 목록 (일반 디자이너 제거)
    embed.add_field(
        name="🖼️ GFX 디자이너 목록",
        value=(
            f"🥇 **상급 디자이너**: {fmt(gfx_high)}\n"
            f"🥈 **중급 디자이너**: {fmt(gfx_mid)}\n"
            f"🥉 **초급 디자이너**: {fmt(gfx_low)}"
        ),
        inline=False
    )

    # 2. Roblox 복장 디자이너 목록 (등급 없이 전체 디자이너 목록만 표시)
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
        cursor = await db.execute("SELECT channel_id, message_id FROM designer_tier_panel WHERE id = 1")
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


@bot.event
async def on_member_update(before, after):
    if before.roles != after.roles:
        await update_designer_tier_panel_message(bot)


@bot.event
async def on_member_remove(member):
    await update_designer_tier_panel_message(bot)


# ==================== [포인트 랭킹 패널 헬퍼] ====================

async def build_point_ranking_embed(guild):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("""
            SELECT user_id, points 
            FROM user_points 
            ORDER BY points DESC 
            LIMIT 10
        """)
        rows = await cursor.fetchall()

    embed = discord.Embed(
        title="🏆 Dialian 포인트 랭킹 (TOP 10)",
        description="실시간으로 동기화되는 포인트 순위입니다! ✨\n*(매월 1일 00시에 포인트가 초기화됩니다)*",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )

    if not rows:
        embed.add_field(name="📊 순위 정보", value="아직 적립된 포인트 데이터가 없습니다.", inline=False)
    else:
        medals = ["🥇 1위", "🥈 2위", "🥉 3위"]
        ranking_list = []
        for idx, (user_id, points) in enumerate(rows, start=1):
            member = await fetch_member_or_none(guild, user_id) if guild else None
            user_display = member.mention if member else f"알 수 없는 유저(`{user_id}`)"
            rank_tag = medals[idx - 1] if idx <= 3 else f"**{idx}위**"
            ranking_list.append(f"{rank_tag} | {user_display} — **`{points:,} P`**")

        embed.add_field(name="📊 실시간 TOP 10", value="\n".join(ranking_list), inline=False)

    embed.set_footer(text="자동 동기화 주기 작동 중 | 매월 1일 포인트 초기화")
    return embed


async def update_point_ranking_message(bot_instance):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT channel_id, message_id FROM point_ranking_panel WHERE id = 1")
        row = await cursor.fetchone()

    if not row:
        return

    channel_id, message_id = row
    channel = bot_instance.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
        embed = await build_point_ranking_embed(channel.guild)
        await message.edit(embed=embed)
    except Exception as e:
        print(f"[랭킹 패널 갱신 오류] {e}")


# ==================== [채널 유효성 및 일일 제한 헬퍼] ====================

async def check_command_channel(ctx):
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.send(f"❌ 해당 명령어는 <#{COMMAND_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.", delete_after=5)
        return False
    return True


async def check_and_increment_daily_limit(user_id: int, action_type: str, max_limit: int = DAILY_ACTION_LIMIT):
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_activity_limits (
                user_id INTEGER,
                action_type TEXT,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, action_type, date)
            )
        """)
        cursor = await db.execute("""
            SELECT count FROM daily_activity_limits
            WHERE user_id = ? AND action_type = ? AND date = ?
        """, (user_id, action_type, today))
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


# ==================== [포인트 감지 이벤트] ====================

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if hasattr(message.channel, "id") and message.channel.id == WORK_SHARE_CHANNEL_ID:
        can_earn, count = await check_and_increment_daily_limit(message.author.id, "work_share")
        if can_earn:
            success = await check_and_add_share_points(message.guild, message.author, message)
            if success:
                try:
                    await message.add_reaction("🪙")
                except Exception:
                    pass

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload):
    if not payload.guild_id or payload.user_id == bot.user.id:
        return

    if payload.channel_id != FEEDBACK_CHANNEL_ID:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    channel = guild.get_channel(payload.channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    if message.author.id == payload.user_id or message.author.bot:
        return

    can_earn, count = await check_and_increment_daily_limit(payload.user_id, "feedback_react")
    if can_earn:
        user = guild.get_member(payload.user_id)
        if user:
            success = await check_and_add_feedback_points(guild, user, message)
            if success:
                try:
                    await message.add_reaction("🪙")
                except Exception:
                    pass


# ==================== [명령어 모음] ====================

@bot.command(name="명령어", aliases=["help", "도움말"])
async def command_list(ctx):
    embed = discord.Embed(
        title="Dialian 명령어 목록",
        description=(
            "**[티켓 및 일반 서비스]**\n"
            "`!티켓생성` `!계좌전송` `!티켓닫기` `!티켓삭제` `!인증패널` `!담당 @유저`\n"
            "`!진행 0|25|50|75|100` `!예상 [시간]` `!완료` `!티켓정보` `!고객` `!소유자변경 @유저` `!청소 1~100`\n"
            "`!계좌등록 @유저 은행 계좌번호 예금주` `!계좌목록` `!계좌삭제 @유저`\n"
            "`!통계` `!진행티켓` `!강제종료`\n\n"
            "**[패널 및 가이드 설정]**\n"
            "`!포인트안내` (포인트 적립 채널에 공지 임베드 전송)\n"
            "`!디자이너등급패널` (디자이너 등급 실시간 패널 생성)\n"
            "`!포인트랭킹` (포인트 실시간 TOP 10 패널 생성)\n\n"
            "**[시스템 & 관리]**\n"
            "`!업데이트확인` `!업데이트` (최신 Git Pull 반영)\n\n"
            "**[포인트 & 프로필]** *(명령어 채널 전용)*\n"
            "`!포인트` `!포인트지급 @유저 금액` `!포인트차감 @유저 금액` `!포인트리셋 @유저`\n\n"
            "**[🎰 오락실 & 미니게임]** *(명령어 채널 전용)*\n"
            "`!뽑기` - 20P 소모\n"
            "`!가위바위보 [가위/바위/보] [배팅포인트]` - 승리 시 약 1.95배!\n"
            "`!묵찌빠 [가위/바위/보] [배팅포인트]` - 승리 시 최대 1.3배!"
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


@bot.command(name="포인트안내")
@commands.has_permissions(administrator=True)
async def send_point_guide_embed(ctx):
    target_channel = bot.get_channel(POINT_INFO_CHANNEL_ID) or ctx.channel

    embed = discord.Embed(
        title="💼 [ 포인트 적립 및 이용 안내 ]",
        description="서버 활동을 통해 포인트를 쌓고, 다양한 혜택과 재미를 즐겨보세요! ✨",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="1️⃣ 포인트 적립 방법 (채널별 안내)",
        value=(
            f"• <#{POINT_RANKING_CHANNEL_ID}> **작품 공유**\n"
            "  - 이미지 첨부 + 20자 이상 작성 시 ➡️ **+15P** *(하루 최대 3회)*\n\n"
            "• **피드백 채널**\n"
            "  - 메시지에 반응(이모지 등) 남길 시 ➡️ **+10P** *(하루 최대 3회, 본인 제외)*\n\n"
            "• **후기 작성**\n"
            "  - GFX / 복장 단품 구매 후기: **30P**\n"
            "  - 2 + 1 묶음 구매 후기: **45P**\n"
            "  - 3 + 1 묶음 구매 후기: **60P**"
        ),
        inline=False
    )

    embed.add_field(
        name="2️⃣ 단골 손님 혜택 (15% 자동 할인)",
        value="**1000P 달성 시 `@Regular Customer/단골 손님` 역할 자동 지급!**\n*(이후 주문하는 모든 커미션에 15% 자동 할인 혜택이 적용됩니다.)*",
        inline=False
    )

    embed.add_field(
        name="3️⃣ 포인트 관련 명령어 & 미니오락실 (봇명령 채널)",
        value=(
            "```\n"
            "[ 포인트 확인 ]\n"
            "!포인트 (또는 !마일리지, !p) [@유저 선택]\n"
            "- 보유 포인트 및 티어/혜택 현황 확인\n\n"
            "[ 포인트 오락실 & 미니게임 ]\n"
            "!뽑기 (또는 !가챠, !럭키드로우)\n"
            "- 20P 소모 / 최대 300P 잭팟!\n\n"
            "!가위바위보 [가위/바위/보] [배팅포인트]\n"
            "- 최소 배팅 10P 이상 / 승리 시 1.95배 지급!\n\n"
            "!묵찌빠 [가위/바위/보] [배팅포인트]\n"
            "- 최소 배팅 20P 이상 / 승리 시 최대 1.3배 지급!\n\n"
            "[ 관리자 전용 ]\n"
            "!포인트지급 [@유저] [금액]\n"
            "!포인트차감 [@유저] [금액]\n"
            "!포인트리셋 [@유저]\n"
            "```"
        ),
        inline=False
    )

    embed.set_footer(text="DDS Point System | 즐거운 서버 활동 되세요!")
    await target_channel.send(embed=embed)
    if target_channel.id != ctx.channel.id:
        await ctx.send(f"✅ <#{POINT_INFO_CHANNEL_ID}> 채널에 포인트 안내 임베드를 전송했습니다.")


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


@bot.command(name="포인트", aliases=["마일리지", "p"])
async def show_points(ctx, member: discord.Member = None):
    if not await check_command_channel(ctx):
        return

    target = member or ctx.author
    points = await get_user_points(target.id)

    if points >= 1000:
        tier_icon = "🥇"
        tier_name = "골드 (최상위 VVIP 단골)"
        color = discord.Color.gold()
    elif points >= 500:
        tier_icon = "🥈"
        tier_name = "실버 (단골 유망주)"
        color = discord.Color.light_grey()
    elif points >= 200:
        tier_icon = "🥉"
        tier_name = "브론즈"
        color = discord.Color.dark_orange()
    else:
        tier_icon = "🌱"
        tier_name = "뉴비"
        color = discord.Color.green()

    embed = discord.Embed(
        title=f"📊 {target.display_name} 님의 프로필",
        color=color
    )
    embed.add_field(name="현재 계급 (티어)", value=f"{tier_icon} **{tier_name}**", inline=False)
    embed.add_field(name="현재 포인트", value=f"`{points} P` / (골드 기준: `1000 P`)", inline=False)

    if points >= 1000:
        embed.add_field(name="🎁 해제된 최고 혜택", value="✅ **골드 단골 손님 (모든 커미션 15% 자동 할인 적용 중)**", inline=False)
    else:
        remaining = 1000 - points
        embed.add_field(name="승급까지 남은 길", value=f"최고 등급 **골드(단골 15% 할인)**까지 **{remaining} P** 남았습니다!", inline=False)

    await ctx.send(embed=embed)


@bot.command(name="뽑기", aliases=["가챠", "럭키드로우"])
async def point_gacha(ctx):
    if not await check_command_channel(ctx):
        return

    current_points = await get_user_points(ctx.author.id)
    cost = GACHA_COST

    if current_points < cost:
        return await ctx.send(f"❌ 포인트가 부족합니다. (현재 `{current_points}P` / 필요 `{cost}P`)")

    await add_user_points(ctx.guild, ctx.author, -cost)

    prizes = [2, 10, 20, 30, 50, 100, 300]
    weights = [60, 15, 15, 6, 3, 0.9, 0.1]
    result = random.choices(prizes, weights=weights, k=1)[0]

    await add_user_points(ctx.guild, ctx.author, result)
    final_points = await get_user_points(ctx.author.id)

    if result == 2:
        color, title, desc = discord.Color.dark_grey(), "😭 아쉬운 꽝!", "위로 포인트 **2P**를 받으셨습니다."
    elif result == 10:
        color, title, desc = discord.Color.light_grey(), "💧 절반 보전!", "소모한 포인트의 절반인 **10P**를 찾았습니다."
    elif result == 20:
        color, title, desc = discord.Color.blue(), "😐 본전치기!", "소모한 20P를 그대로 찾아왔습니다."
    elif result == 300:
        color, title, desc = discord.Color.magenta(), "🔥 극악의 300P 잭팟 터짐!!!", f"0.1%의 기적을 뚫고 무려 **{result}P**를 획득했습니다!"
    elif result >= 50:
        color, title, desc = discord.Color.gold(), "🎉 축하합니다! 대박 당첨!", f"**+{result}P**를 얻으셨습니다!"
    else:
        color, title, desc = discord.Color.green(), "✨ 소소한 이득!", f"**+{result}P**를 획득했습니다!"

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.add_field(name="현재 잔여 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="가위바위보")
async def rock_paper_scissors(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!가위바위보 [가위/바위/보] [배팅포인트]`")

    if bet < 10:
        return await ctx.send("❌ 최소 배팅 금액은 `10 P` 이상이어야 합니다.")

    current_points = await get_user_points(ctx.author.id)
    if current_points < bet:
        return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points}P`)")

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
            description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n🎉 승리하여 **+{win_profit}P** (수수료 5% 제외)를 획득했습니다!",
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
            description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n😭 패배하여 `{bet}P`를 잃었습니다.",
            color=discord.Color.red()
        )

    embed.add_field(name="현재 보유 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="묵찌빠")
async def muk_jji_bba(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!묵찌빠 [가위/바위/보] [배팅포인트]`")

    if bet < 20:
        return await ctx.send("❌ 묵찌빠 최소 배팅 금액은 `20 P` 이상이어야 합니다.")

    current_points = await get_user_points(ctx.author.id)
    if current_points < bet:
        return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points}P`)")

    bot_choice1 = random.choice(choices)
    if choice == bot_choice1:
        bot_choice1 = random.choice([c for c in choices if c != choice])

    user_attacker = (
        (choice == "가위" and bot_choice1 == "보") or
        (choice == "바위" and bot_choice1 == "가위") or
        (choice == "보" and bot_choice1 == "바위")
    )

    bot_choice2 = random.choice(choices)
    user_choice2 = choice

    embed = discord.Embed(title="👊✌️🖐️ 스릴만점 묵찌빠 대결!", color=discord.Color.blurple())
    embed.add_field(
        name="1라운드 (주도권 잡기)",
        value=f"유저: **{choice}** vs 봇: **{bot_choice1}** ➔ **{'유저' if user_attacker else '봇'}** 공격 선제 잡기!",
        inline=False
    )

    if user_choice2 == bot_choice2:
        if user_attacker:
            win_amount = int(bet * 1.3)
            await add_user_points(ctx.guild, ctx.author, win_amount)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(name="2라운드", value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}**\n\n🔥 **공격 성공!** **+{win_amount}P** 획득!", inline=False)
            embed.color = discord.Color.gold()
        else:
            await add_user_points(ctx.guild, ctx.author, -bet)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(name="2라운드", value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}**\n\n💀 방어 실패로 `{bet}P`를 잃었습니다.", inline=False)
            embed.color = discord.Color.dark_red()
    else:
        bot_wins_final = random.choices([True, False], weights=[55, 45])[0]
        if not bot_wins_final:
            win_amount = int(bet * 1.1)
            await add_user_points(ctx.guild, ctx.author, win_amount)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(name="2라운드", value=f"유저 승리! **+{win_amount}P** 획득!", inline=False)
            embed.color = discord.Color.green()
        else:
            await add_user_points(ctx.guild, ctx.author, -bet)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(name="2라운드", value=f"패배하여 `{bet}P`를 잃었습니다.", inline=False)
            embed.color = discord.Color.red()

    embed.add_field(name="현재 보유 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="포인트지급")
@commands.has_permissions(administrator=True)
async def give_points(ctx, member: discord.Member, amount: int):
    new_points = await add_user_points(ctx.guild, member, amount)
    await ctx.send(f"✅ {member.mention} 님에게 `{amount} P`를 지급했습니다. (현재: `{new_points} P`)")


@bot.command(name="포인트차감")
@commands.has_permissions(administrator=True)
async def remove_points(ctx, member: discord.Member, amount: int):
    new_points = await add_user_points(ctx.guild, member, -amount)
    await ctx.send(f"✅ {member.mention} 님의 포인트를 `{amount} P` 차감했습니다. (현재: `{new_points} P`)")


@bot.command(name="포인트리셋")
@commands.has_permissions(administrator=True)
async def reset_points(ctx, member: discord.Member):
    current_points = await get_user_points(member.id)
    if current_points > 0:
        await add_user_points(ctx.guild, member, -current_points)
    await ctx.send(f"🔄 {member.mention} 님의 포인트를 `0 P`로 초기화했습니다.")


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
            embed.set_footer(text="⚠️ Python 코드 변경사항을 완벽히 적용하려면 프로세스 재시작이 필요할 수 있습니다.")

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


# ==================== [보안 및 유틸리티 헬퍼] ====================

def parse_mention_id(text):
    if not text:
        return None
    match = re.search(r"<@!?(\d+)>", str(text))
    return int(match.group(1)) if match else None


def is_ticket_channel(channel):
    return isinstance(channel, discord.TextChannel) and channel.name.startswith("티켓-")


def is_ticket_or_archive_channel(channel):
    return isinstance(channel, discord.TextChannel) and (channel.name.startswith("티켓-") or channel.name.startswith("보관-티켓-"))


async def find_ticket_owner(channel):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT customer_id FROM commissions WHERE ticket_channel = ?", (channel.id,))
        row = await cursor.fetchone()
        if row and row[0]:
            member = await fetch_member_or_none(channel.guild, row[0])
            if member:
                return member

    try:
        if channel.topic:
            match = re.search(r"\d+", channel.topic)
            if match:
                return await fetch_member_or_none(channel.guild, int(match.group(0)))
    except (TypeError, ValueError):
        pass

    async for msg in channel.history(limit=5, oldest_first=True):
        if msg.mentions:
            return msg.mentions[0]
    return None


async def find_ticket_designer_id(channel):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT designer_id FROM commissions WHERE ticket_channel = ?", (channel.id,))
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


def can_manage_ticket(member, user_id, designer_id):
    if member is None:
        return False
    if member.guild_permissions.administrator:
        return True
    if designer_id is not None:
        return user_id == designer_id
    return has_designer_role(member)


async def fetch_member_or_none(guild, member_id):
    if not member_id:
        return None
    member = guild.get_member(member_id)
    if member:
        return member
    try:
        return await guild.fetch_member(member_id)
    except Exception:
        return None


async def update_commission_progress(channel, progress):
    now = datetime.now().isoformat()
    status = "completed" if progress == 100 else "in_progress"
    async with aiosqlite.connect(DATABASE) as db:
        if progress == 100:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?, status = ?, completed_at = COALESCE(completed_at, ?), updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now, now, channel.id)
            )
        else:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?, status = ?, updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now, channel.id)
            )
        await db.commit()


async def upsert_commission_record(data):
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


async def send_payment_info(channel, designer_id):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            "SELECT bank_name, account_number, holder FROM bank_accounts WHERE developer_id = ?",
            (designer_id,)
        )
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


# ==================== [티켓 패널 생성 및 업무 명령어] ====================

@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):
    file = discord.File("price.png", filename="price.png")
    file2 = discord.File("price2.png", filename="price2.png")

    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description="상담, 구매 진행, 문의사항이 있으시다면\n아래 📩 버튼을 눌러주세요!\n\n📌 구매 전 가격표를 확인해주세요.",
        color=0x5865F2
    )
    embed.set_image(url="attachment://price.png")

    embed2 = discord.Embed(color=0x5865F2)
    embed2.set_image(url="attachment://price2.png")

    await ctx.send(files=[file, file2], embeds=[embed, embed2], view=CombinedTicketOpenView())


@bot.command(name="통계")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    embed = await build_monthly_stats_embed(ctx.guild)
    message = await ctx.send(embed=embed)
    await save_monthly_stats_message(message)
    await ctx.reply("✅ 월간 통계 패널을 등록했습니다.", mention_author=False, delete_after=5)


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
        cursor = await db.execute(query, params)
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


@bot.command(name="계좌목록", aliases=["계좌리스트"])
@commands.has_permissions(administrator=True)
async def list_bank_accounts(ctx):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT developer_id, bank_name, account_number, holder FROM bank_accounts")
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
    else:
        await ctx.send(f"❌ {member.mention} 님의 등록된 계좌 정보를 찾을 수 없습니다.")


@bot.command(name="계좌전송", aliases=["계좌번호", "결제정보", "결제"])
async def send_bank_to_ticket(ctx, member: discord.Member = None):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    author = ctx.guild.get_member(ctx.author.id)
    is_admin = author and author.guild_permissions.administrator
    designer_id = member.id if member else await find_ticket_designer_id(ctx.channel)

    if designer_id is None and has_designer_role(author):
        designer_id = ctx.author.id

    if designer_id is None:
        return await ctx.send("❌ 담당 디자이너를 찾지 못했습니다.")

    if not is_admin and ctx.author.id != designer_id:
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 계좌를 전송할 수 있습니다.")

    if not await send_payment_info(ctx.channel, designer_id):
        return await ctx.send("❌ 담당 디자이너의 계좌가 등록되어 있지 않습니다.")

    await ctx.reply("✅ 결제 정보를 티켓에 전송했습니다.", mention_author=False, delete_after=3)


@bot.command(name="담당변경", aliases=["담당", "담당자"])
@commands.has_permissions(administrator=True)
async def change_designer(ctx, designer: discord.Member):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE commissions SET designer_id = ?, updated_at = ? WHERE ticket_channel = ?",
            (designer.id, datetime.now().isoformat(), ctx.channel.id)
        )
        await db.commit()

    try:
        await ctx.channel.set_permissions(designer, read_messages=True, send_messages=True, attach_files=True)
    except Exception:
        pass

    await ctx.send(f"✅ 티켓 담당 디자이너가 {designer.mention} 님으로 변경되었습니다.")


@bot.command(name="티켓닫기", aliases=["티켓종료", "닫기"])
async def close_ticket_by_command(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    closer = guild.get_member(ctx.author.id)

    if not can_manage_ticket(closer, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 티켓을 종료할 수 있습니다.")

    notice = await ctx.send("🔒 티켓 종료 처리 중입니다.")
    designer = await fetch_member_or_none(guild, designer_id)

    if designer:
        await delete_ticket_dm_messages(bot.user, designer, channel)

    await update_commission_progress(channel, 100)
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
    deleter = guild.get_member(ctx.author.id)

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
        cursor = await db.execute(
            "SELECT customer_id, designer_id, category, status, progress, created_at FROM commissions WHERE ticket_channel = ?",
            (ctx.channel.id,)
        )
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
        await ctx.send("❌ 티켓 주문 고객 정보를 찾을 수 없습니다.")


@bot.command(name="소유자변경", aliases=["고객변경"])
@commands.has_permissions(administrator=True)
async def change_ticket_owner(ctx, new_owner: discord.Member):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE commissions SET customer_id = ?, updated_at = ? WHERE ticket_channel = ?",
            (new_owner.id, datetime.now().isoformat(), ctx.channel.id)
        )
        await db.commit()

    try:
        await ctx.channel.set_permissions(new_owner, read_messages=True, send_messages=True, attach_files=True)
    except Exception:
        pass

    await ctx.send(f"✅ 티켓 소유자(고객)가 {new_owner.mention} 님으로 변경되었습니다.")


@bot.command(name="강제종료")
@commands.has_permissions(administrator=True)
async def force_close_ticket(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    await ctx.send("🚨 관리자 권한으로 티켓을 강제 종료하고 보관합니다.")
    await archive_ticket_channel(ctx.channel)


@bot.command(name="청소")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    if amount < 1 or amount > 100:
        return await ctx.send("사용법: `!청소 1~100`")
    await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"✅ {amount}개의 메시지를 삭제했습니다.")
    await msg.delete(delay=3)


@bot.command(name="인증패널")
@commands.has_permissions(administrator=True)
async def verify_panel(ctx):
    embed = discord.Embed(title="✅ 서버 인증", description="아래 버튼을 눌러 인증을 완료해주세요.", color=discord.Color.green())
    await ctx.send(embed=embed, view=VerifyView())


# ==================== [자동 반복 태스크] ====================

@tasks.loop(hours=12)
async def auto_chat_guide_loop():
    kr_channel = bot.get_channel(KR_CHAT_CHANNEL_ID)
    if kr_channel:
        embed_kr = discord.Embed(
            title="✨ DDS (Design & Developer Service) 공식 가이드",
            description=(
                "안녕하세요! **DDS 공식 커뮤니티**에 오신 것을 환영합니다! 🎉\n"
                "서버를 효율적으로 이용하실 수 있도록 주요 채널 안내를 드립니다."
            ),
            color=discord.Color.blurple()
        )
        embed_kr.add_field(
            name="📌 주요 이용 안내 채널",
            value=(
                "• <#1505102694917079132> : 커미션 주문 및 문의/지원 신청\n"
                "• <#1505178799950532720> : 디자이너 샘플 및 예시작 감상\n"
                f"• <#{DESIGNER_TIER_CHANNEL_ID}> : 디자이너 등급 및 분야 현황\n"
                "• <#1521001578239361155> : 디자이너 작업 완료 통계\n"
                f"• <#{FEEDBACK_CHANNEL_ID}> : 실제 이용 고객님들의 솔직한 후기"
            ),
            inline=False
        )
        embed_kr.add_field(
            name="🪙 포인트 & 혜택 시스템",
            value=(
                f"• <#{POINT_RANKING_CHANNEL_ID}> : 포인트 실시간 랭킹 확인\n"
                f"• <#{POINT_INFO_CHANNEL_ID}> : 포인트 적립 방법 및 단골(15% 할인) 혜택 안내"
            ),
            inline=False
        )
        embed_kr.set_footer(text="자동 가이드 공지 | 12시간 주기 업데이트")
        try:
            await kr_channel.send(embed=embed_kr)
        except Exception as e:
            print(f"[한챗 공지 실패] {e}")

    en_channel = bot.get_channel(EN_CHAT_CHANNEL_ID)
    if en_channel:
        embed_en = discord.Embed(
            title="✨ DDS (Design & Developer Service) Guide",
            description=(
                "Welcome to **DDS Official Server**! 🎉\n"
                "Here is a quick directory of our main channels to help you get started."
            ),
            color=discord.Color.blue()
        )
        embed_en.add_field(
            name="📌 Essential Channels",
            value=(
                "• <#1505102694917079132> : Order commissions & Partner/Dev inquiries\n"
                "• <#1505178799950532720> : Designer portfolio & sample showcase\n"
                f"• <#{DESIGNER_TIER_CHANNEL_ID}> : Designer ranks & categories\n"
                "• <#1521001578239361155> : Designer completion statistics\n"
                f"• <#{FEEDBACK_CHANNEL_ID}> : Customer reviews & feedback"
            ),
            inline=False
        )
        embed_en.add_field(
            name="🪙 Points & Rewards",
            value=(
                f"• <#{POINT_RANKING_CHANNEL_ID}> : Real-time Point Leaderboard\n"
                f"• <#{POINT_INFO_CHANNEL_ID}> : How to earn points & VIP perks (15% OFF)"
            ),
            inline=False
        )
        embed_en.set_footer(text="Auto Guide Notice | Updates every 12 hours")
        try:
            await en_channel.send(embed=embed_en)
        except Exception as e:
            print(f"[영챗 공지 실패] {e}")


@tasks.loop(minutes=30)
async def monthly_stats_updater():
    try:
        await update_monthly_stats_message(bot)
    except Exception as e:
        print(f"[월간 통계 갱신 실패] {e}")


@tasks.loop(hours=6)
async def point_ranking_updater():
    await update_point_ranking_message(bot)


@tasks.loop(hours=1)
async def monthly_point_reset_task():
    now = datetime.now()
    if now.day == 1:
        current_ym = now.strftime("%Y-%m")
        async with aiosqlite.connect(DATABASE) as db:
            cursor = await db.execute("SELECT year_month FROM point_reset_logs WHERE year_month = ?", (current_ym,))
            if not await cursor.fetchone():
                await db.execute("UPDATE user_points SET points = 0")
                await db.execute("INSERT INTO point_reset_logs (year_month) VALUES (?)", (current_ym,))
                await db.commit()
                await update_point_ranking_message(bot)


@tasks.loop(hours=24)
async def database_backup_task():
    try:
        await backup_database()
    except Exception as error:
        print(f"[DB 백업 실패] {error}")


@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"[명령어 에러] {ctx.command}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)


# ==================== [봇 초기화 및 실행] ====================

@bot.event
async def setup_hook():
    await create_tables()
    await init_extended_db()

    if os.getenv("OPENAI_API_KEY"):
        try:
            await bot.load_extension("database.services.auto_translator")
        except Exception as e:
            print(f"[자동번역 로드 실패] {e}")

    # 인자 없이 생성이 확실한 클래스들만 기본 매핑
    default_views = [
        CombinedTicketOpenView,
        CategorySelectView,
        VerifyView,
        ClaimTicketView,
    ]

    for view_cls in default_views:
        try:
            bot.add_view(view_cls())
        except Exception as e:
            print(f"❌ 영속성 뷰 등록 실패 ({view_cls.__name__}): {e}")

    optional_arg_views = [
        ("StarRatingView", lambda: StarRatingView(designer_id=None)),
        ("PaymentView", lambda: PaymentView()),
        ("TicketCloseView", lambda: TicketCloseView()),
    ]

    for name, view_factory in optional_arg_views:
        try:
            bot.add_view(view_factory())
        except Exception as e:
            print(f"⚠️ {name} 영속성 뷰 등록 실패: {e}")

    print("✨ 영속성 뷰 및 DB 로드 완료!")


@bot.event
async def on_ready():
    global update_notice_sent, daily_notice

    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")

    if daily_notice is None:
        daily_notice = DailyNotice(bot)

    if not monthly_stats_updater.is_running():
        monthly_stats_updater.start()
    if not point_ranking_updater.is_running():
        point_ranking_updater.start()
    if not monthly_point_reset_task.is_running():
        monthly_point_reset_task.start()
    if not database_backup_task.is_running():
        database_backup_task.start()
    if not auto_chat_guide_loop.is_running():
        auto_chat_guide_loop.start()

    try:
        await update_monthly_stats_message(bot)
        await update_designer_tier_panel_message(bot)
    except Exception:
        pass


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
