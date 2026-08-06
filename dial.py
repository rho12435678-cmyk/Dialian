import asyncio
import os
import random
import re
import subprocess
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import ui
from discord.ext import commands, tasks

from config import *
from database.backups import backup_database
from database.database import DATABASE, create_tables
from database.DailyNotice import DailyNotice
from database.services.points import (
    add_user_points,
    check_and_add_feedback_points,
    check_and_add_share_points,
    get_user_points,
)
from database.views.close_ticket import (
    TicketCloseView,
    archive_ticket_channel,
    delete_all_bot_dm_messages,
    delete_ticket_channel,
    delete_ticket_dm_messages,
    has_designer_role,
)
from database.views.payment_view import PaymentView
from database.views.progress_view import ProgressView
from database.views.review_view import StarRatingView
from database.views.ticket_view import TicketOpenView
from database.views.verify_view import VerifyView

TOKEN = os.getenv("TOKEN")
POINT_RANKING_CHANNEL_ID = 1532599012316938321  # 랭킹 패널 전용 채널 ID


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
persistent_views_registered = False
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
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                message_id INTEGER PRIMARY KEY
            )
        """)
        cursor = await db.execute(
            f"INSERT OR IGNORE INTO {table_name}(message_id) VALUES (?)" ,
            (message_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


@bot.check
async def prevent_duplicate_command_processing(ctx):
    return await claim_once("processed_commands", ctx.message.id)


# ==================== [통계 평점 및 상세 항목 복구 월간 통계 함수] ====================

async def build_monthly_stats_embed(guild: discord.Guild) -> discord.Embed:
    current_month = datetime.now().strftime("%Y-%m")

    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            """
            SELECT 
                COALESCE(AVG(CAST(stars AS FLOAT)), 0.0),
                COUNT(stars)
            FROM reviews
            WHERE created_at LIKE ? OR strftime('%Y-%m', created_at) = ?
            """,
            (f"{current_month}%", current_month)
        ) as cursor:
            row = await cursor.fetchone()
            avg_stars = round(row[0], 2) if row and row[0] else 0.00
            review_count = row[1] if row else 0

        async with db.execute(
            """
            SELECT status, COUNT(*) 
            FROM commissions 
            WHERE created_at LIKE ?
            GROUP BY status
            """,
            (f"{current_month}%",)
        ) as cursor:
            status_data = dict(await cursor.fetchall())

        top_designer_text = "집계 데이터 없음"
        async with db.execute(
            """
            SELECT designer_id, COUNT(*) as cnt
            FROM commissions
            WHERE (created_at LIKE ? OR strftime('%Y-%m', created_at) = ?) AND designer_id IS NOT NULL
            GROUP BY designer_id
            ORDER BY cnt DESC
            LIMIT 1
            """,
            (f"{current_month}%", current_month)
        ) as cursor:
            top_row = await cursor.fetchone()
            if top_row and guild:
                member = guild.get_member(top_row[0])
                if member:
                    top_designer_text = f"{member.mention} ({top_row[1]}건 완료)"

    total = sum(status_data.values())
    completed = status_data.get("completed", 0)
    in_progress = status_data.get("in_progress", 0)
    cancelled = status_data.get("cancelled", 0)

    embed = discord.Embed(
        title=f"📅 {datetime.now().strftime('%Y년 %m월')} Dial Design Studio 통계",
        color=0x5865F2,
        timestamp=datetime.now()
    )

    embed.description = (
        f"📦 **총 주문** : {total}건\n"
        f"✅ **완료** : {completed}건\n"
        f"⏳ **진행 중** : {in_progress}건\n"
        f"❌ **취소** : {cancelled}건\n"
        f"🏆 **TOP Designer** : {top_designer_text}\n"
        f"⭐ **평균 평점** : {avg_stars:.2f} / 5.0\n"
        f"📝 **후기 참여** : {review_count}개"
    )

    embed.set_footer(text="월간 통계는 주기적으로 자동 갱신됩니다.")
    return embed


async def save_monthly_stats_message(message: discord.Message):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS monthly_stats_panel (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER,
                message_id INTEGER
            )
        """)
        await db.execute("""
            INSERT INTO monthly_stats_panel (id, channel_id, message_id)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
        """, (message.channel.id, message.id))
        await db.commit()


async def update_monthly_stats_message(bot_instance):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT channel_id, message_id FROM monthly_stats_panel WHERE id = 1")
        row = await cursor.fetchone()

    if not row:
        return

    channel_id, message_id = row
    channel = bot_instance.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
        embed = await build_monthly_stats_embed(channel.guild)
        await message.edit(embed=embed)
    except Exception as e:
        print(f"[통계 패널 갱신 오류] {e}")


# ==================== [티켓 채널 안내 임베드 및 신청서 UI] ====================

async def send_ticket_guides(channel: discord.TextChannel, user: discord.User, designer_id: int = None):
    designer_mention = f"<@{designer_id}>" if designer_id else "미배정"
    
    guide_embed = discord.Embed(
        title="📌 커미션 안내 사항",
        description=(
            "**기본 안내**\n"
            "1. 가격 협상(네고) 안됨\n"
            "2. 작업 중 과도한 수정요청 삼가\n"
            "3. 모든 커미션은 선 결제 후 작업을 원칙으로 함\n"
            "4. 커미션 중 철회 시 수수료 부담\n\n"
            "**철회 수수료**\n"
            "작업 전 철회 : 전액 환불\n\n"
            "작업 후 철회 :\n"
            "상급 : 3,000원\n"
            "중급 : 2,000원\n"
            "초급 : 1,500원\n\n"
            "복장 커미션 : 1,500원"
        ),
        color=0xFEE75C
    )

    progress_embed = discord.Embed(
        title="📌 커미션 진행",
        description=(
            f"👨‍💻 담당 디자이너 : {designer_mention}\n\n"
            "📌 상태 : 🟢 상담중\n"
            "📊 진행률 : 0%\n"
            "⏰ 예상 완료 : 작업 시작 전"
        ),
        color=0x5865F2
    )

    ref_embed = discord.Embed(
        title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
        description=(
            f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
            "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 구체적으로 올려주세요!"
        ),
        color=0x5865F2
    )
    ref_embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")

    await channel.send(embeds=[guide_embed, progress_embed, ref_embed])


class CustomCommissionModal(ui.Modal):
    def __init__(self, category: str, bundle_type: str, designer_id: int = None):
        super().__init__(title=f"🎨 {category} [{bundle_type}] 신청서")
        self.category = category
        self.bundle_type = bundle_type
        self.designer_id = designer_id

        self.roblox_name = ui.TextInput(
            label="🎮 Roblox 닉네임",
            placeholder="작품에 반영될 로블록스 닉네임을 작성해주세요.",
            required=True
        )
        self.add_item(self.roblox_name)

        if category == "GFX":
            self.gfx_genre = ui.TextInput(
                label="🎬 원하는 GFX 장르",
                placeholder="예: 밀리터리(군대), 판타지, 일상, SF 등",
                required=True,
                max_length=100
            )
            self.add_item(self.gfx_genre)

        if bundle_type == "단품 (1개)":
            self.details = ui.TextInput(
                label="🖌️ 원하는 스타일 및 설명",
                style=discord.TextStyle.paragraph,
                placeholder="원하시는 콘셉트, 구도, 색감, 의상 등을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.details)
        elif bundle_type == "2+1 묶음":
            self.details = ui.TextInput(
                label="📝 제작 순서별 상세 요구사항 (3개)",
                style=discord.TextStyle.paragraph,
                placeholder="1번, 2번, 3번 작품에 대한 요구사항을 작성해주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.details)
        else:
            self.details = ui.TextInput(
                label="📝 1~3번째 작품 요구사항",
                style=discord.TextStyle.paragraph,
                placeholder="1, 2, 3번째 작품에 대한 상세 요구사항을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.details)

            self.fourth_details = ui.TextInput(
                label="🎁 4번째 작품 요구사항 (3+1 보너스)",
                style=discord.TextStyle.paragraph,
                placeholder="4번째 작품에 대한 요구사항이나 추가 설명을 적어주세요.",
                required=True,
                max_length=1000
            )
            self.add_item(self.fourth_details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        
        # 티켓 채널 권한 설정 (명령어, 이미지 파일 전송 권한 보장)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                use_application_commands=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                attach_files=True,
                embed_links=True,
                use_application_commands=True
            )
        }

        # 선택된 담당 디자이너가 있다면 해당 디자이너 채널 접근 권한 추가
        if self.designer_id:
            designer_member = guild.get_member(self.designer_id)
            if designer_member:
                overwrites[designer_member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    use_application_commands=True
                )

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            topic=str(interaction.user.id),
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"📋 {self.category} 커미션 신청서 ({self.bundle_type})",
            color=0x57F287
        )
        embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_name.value, inline=False)
        
        if hasattr(self, "gfx_genre"):
            embed.add_field(name="🎬 GFX 장르", value=self.gfx_genre.value, inline=False)
            
        embed.add_field(name="📌 요청 상세 내용", value=self.details.value, inline=False)
        
        if hasattr(self, "fourth_details"):
            embed.add_field(name="🎁 4번째 작품 요구사항", value=self.fourth_details.value, inline=False)

        await ticket_channel.send(content=interaction.user.mention, embed=embed)
        await send_ticket_guides(ticket_channel, interaction.user, self.designer_id)

        now = datetime.now().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                INSERT INTO commissions (ticket_channel, customer_id, designer_id, category, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'in_progress', 0, ?, ?)
            """, (ticket_channel.id, interaction.user.id, self.designer_id, self.category, now, now))
            await db.commit()

        await interaction.followup.send(
            f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}",
            ephemeral=True
        )


# ==================== [디자이너 선택 드롭다운 UI] ====================

class DesignerSelect(ui.Select):
    def __init__(self, category: str, bundle_type: str, guild: discord.Guild):
        self.category = category
        self.bundle_type = bundle_type

        options = [
            discord.SelectOption(
                label="미지정 (추후 배정)", 
                value="none", 
                description="담당자를 나중에 배정받습니다."
            )
        ]

        # config.py의 DESIGNER_ROLE_IDS 사용
        role_key = "gfx" if category == "GFX" else "uniform"
        role_id = DESIGNER_ROLE_IDS.get(role_key) if isinstance(DESIGNER_ROLE_IDS, dict) else None

        if guild and role_id:
            role = guild.get_role(role_id)
            if role:
                for member in role.members:
                    options.append(
                        discord.SelectOption(
                            label=member.display_name,
                            value=str(member.id),
                            description=f"{category} 담당 디자이너"
                        )
                    )

        super().__init__(
            placeholder="👨‍💻 담당 디자이너를 선택해주세요",
            options=options[:25],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        designer_id = None if self.values[0] == "none" else int(self.values[0])
        modal = CustomCommissionModal(
            category=self.category,
            bundle_type=self.bundle_type,
            designer_id=designer_id
        )
        await interaction.response.send_modal(modal)


class DesignerSelectView(ui.View):
    def __init__(self, category: str, bundle_type: str, guild: discord.Guild):
        super().__init__(timeout=120)
        self.add_item(DesignerSelect(category, bundle_type, guild))


class BundleSelectView(ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.category = category

    async def prompt_designer(self, interaction: discord.Interaction, bundle_type: str):
        view = DesignerSelectView(self.category, bundle_type, interaction.guild)
        await interaction.response.edit_message(
            content=f"👨‍💻 **{self.category} [{bundle_type}]** - 작업을 진행할 담당 디자이너를 선택해주세요.",
            view=view
        )

    @ui.button(label="1개 (단품)", style=discord.ButtonStyle.secondary, custom_id="bundle_single_btn")
    async def select_single(self, interaction: discord.Interaction, button: ui.Button):
        await self.prompt_designer(interaction, "단품 (1개)")

    @ui.button(label="🎁 2+1 묶음 (총 3개)", style=discord.ButtonStyle.primary, custom_id="bundle_21_btn")
    async def select_21(self, interaction: discord.Interaction, button: ui.Button):
        await self.prompt_designer(interaction, "2+1 묶음")

    @ui.button(label="🎁 3+1 묶음 (총 4개)", style=discord.ButtonStyle.success, custom_id="bundle_31_btn")
    async def select_31(self, interaction: discord.Interaction, button: ui.Button):
        await self.prompt_designer(interaction, "3+1 묶음")


# ==================== [개발자 지원 모달 및 뷰 추가] ====================

class DeveloperApplyModal(ui.Modal):
    def __init__(self):
        super().__init__(title="🎨 Dialian 디자이너/개발자 지원서")

        self.portfolio = ui.TextInput(
            label="📌 포트폴리오 링크 또는 경력 소개",
            style=discord.TextStyle.paragraph,
            placeholder="자신의 작업물 링크(픽시브, 트위터 등)나 주요 경력을 적어주세요.",
            required=True,
            max_length=1000
        )
        self.add_item(self.portfolio)

        self.tools = ui.TextInput(
            label="🛠️ 다룰 수 있는 툴 및 자신 있는 분야",
            placeholder="예: Blender, Photoshop, Roblox Studio, GFX, 의상 제작 등",
            required=True,
            max_length=200
        )
        self.add_item(self.tools)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"지원-{interaction.user.name}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                use_application_commands=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                use_application_commands=True
            )
        }

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            topic=str(interaction.user.id),
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"📋 {interaction.user.display_name} 님의 개발자/디자이너 지원서",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📌 포트폴리오 / 경력", value=self.portfolio.value, inline=False)
        embed.add_field(name="🛠️ 사용 가능 툴", value=self.tools.value, inline=False)

        await ticket_channel.send(content=f"{interaction.user.mention} 님의 지원서가 접수되었습니다. 관리자의 심사를 기다려주세요!", embed=embed)

        await interaction.followup.send(
            f"✅ 지원 티켓 채널이 생성되었습니다: {ticket_channel.mention}",
            ephemeral=True
        )


class CustomCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎨 GFX", style=discord.ButtonStyle.primary, custom_id="cat_gfx_btn")
    async def click_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "🎨 **GFX 구매 수량을 선택해주세요.**",
            view=BundleSelectView("GFX"),
            ephemeral=True
        )

    @ui.button(label="👕 Roblox 복장", style=discord.ButtonStyle.primary, custom_id="cat_clothes_btn")
    async def click_clothes(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "👕 **Roblox 복장 구매 수량을 선택해주세요.**",
            view=BundleSelectView("Roblox 복장"),
            ephemeral=True
        )

    @ui.button(label="💡 개발자 지원", style=discord.ButtonStyle.success, custom_id="cat_developer_apply_btn")
    async def click_developer_apply(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(DeveloperApplyModal())


# ==================== [포인트 랭킹 전용 DB 및 헬퍼] ====================

async def init_ranking_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS point_ranking_panel (
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
        await db.commit()


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
        description="6시간마다 실시간으로 동기화되는 포인트 순위입니다! ✨\n*(매월 1일 00시에 포인트가 초기화됩니다)*",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )

    if not rows:
        embed.add_field(name="📊 순위 정보", value="아직 적립된 포인트 데이터가 없습니다.", inline=False)
    else:
        medals = ["🥇 1위", "🥈 2위", "🥉 3위"]
        ranking_list = []
        
        for idx, (user_id, points) in enumerate(rows, start=1):
            member = guild.get_member(user_id) if guild else None
            user_display = member.mention if member else f"알 수 없는 유저(`{user_id}`)"
            rank_tag = medals[idx - 1] if idx <= 3 else f"**{idx}위**"
            ranking_list.append(f"{rank_tag} | {user_display} — **`{points:,} P`**")

        embed.add_field(
            name="📊 실시간 TOP 10",
            value="\n".join(ranking_list),
            inline=False
        )

    embed.set_footer(text="자동 동기화: 6시간 주기 | 매월 1일 포인트 초기화")
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


async def check_command_channel(ctx):
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.send(
            f"❌ 해당 명령어는 <#{COMMAND_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.",
            delete_after=5
        )
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
            "`!티켓생성` `!계좌전송` `!티켓닫기` `!티켓삭제` `!인증패널`\n"
            "`!진행 0|25|50|75|100` `!예상 1일|2일|3일` `!완료` `!청소 1~100`\n"
            "`!계좌등록` `!계좌목록` `!계좌삭제` `!통계` `!통계동기화`\n"
            "`!진행티켓` `!통계수정 티켓ID 진행중|완료|취소 [진행률]`\n"
            "`!진행티켓종료 티켓ID` `!진행티켓삭제 티켓ID`\n\n"
            "**[포인트 & 프로필]** *(명령어 채널 전용)*\n"
            "`!포인트` `!포인트지급 @유저 금액` `!포인트차감 @유저 금액` `!포인트리셋 @유저`\n\n"
            "**[🎰 오락실 & 미니게임]** *(명령어 채널 전용)*\n"
            "`!뽑기` - 20P 소모\n"
            "`!가위바위보 [가위/바위/보] [배팅포인트]`\n"
            "`!묵찌빠 [가위/바위/보] [배팅포인트]`\n\n"
            "**[명예 및 랭킹]**\n"
            "`!포인트랭킹`"
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


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

    embed = discord.Embed(title=f"📊 {target.display_name} 님의 프로필", color=color)
    embed.add_field(name="현재 계급 (티어)", value=f"{tier_icon} **{tier_name}**", inline=False)
    embed.add_field(name="현재 포인트", value=f"`{points} P` / (골드 기준: `1000 P`)", inline=False)
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
    
    embed = discord.Embed(title="🎉 뽑기 결과", description=f"당첨 포인트: **+{result}P**", color=discord.Color.green())
    embed.add_field(name="현재 잔여 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):
    file = discord.File("price.png", filename="price.png")
    file2 = discord.File("price2.png", filename="price2.png")

    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description=(
            "상담, 구매 진행, 문의사항이 있으시다면\n"
            "아래 📩 버튼을 눌러주세요!\n\n"
            "📌 구매 전 가격표를 확인해주세요."
        ),
        color=0x5865F2
    )
    embed.set_image(url="attachment://price.png")

    embed2 = discord.Embed(color=0x5865F2)
    embed2.set_image(url="attachment://price2.png")

    await ctx.send(
        files=[file, file2],
        embeds=[embed, embed2],
        view=CustomCategoryView()
    )


@bot.command(name="통계")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    embed = await build_monthly_stats_embed(ctx.guild)
    message = await ctx.send(embed=embed)
    await save_monthly_stats_message(message)
    await ctx.reply("✅ 월간 통계 패널을 등록했습니다.", mention_author=False, delete_after=5)


# ==================== [유틸리티 및 보안 함수] ====================

def mask_account(account_number):
    digits = re.sub(r"\D", "", account_number)
    if len(digits) <= 4:
        return "****"
    return f"{digits[:3]}****{digits[-4:]}"


def parse_mention_id(text):
    match = re.search(r"<@!?(\d+)>", text or "")
    return int(match.group(1)) if match else None


def is_ticket_channel(channel):
    return isinstance(channel, discord.TextChannel) and channel.name.startswith("티켓-")


def is_ticket_or_archive_channel(channel):
    return isinstance(channel, discord.TextChannel) and (
        channel.name.startswith("티켓-") or channel.name.startswith("보관-티켓-")
    )


async def find_ticket_owner(channel):
    try:
        if channel.topic:
            return channel.guild.get_member(int(channel.topic))
    except (TypeError, ValueError):
        pass
    async for msg in channel.history(limit=5, oldest_first=True):
        if msg.mentions:
            return msg.mentions[0]
    return None


async def find_ticket_designer_id(channel):
    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            for field in embed.fields:
                if field.name == "👨‍💻 담당 디자이너":
                    designer_id = parse_mention_id(field.value)
                    if designer_id:
                        return designer_id
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


async def send_payment_info(channel, designer_id):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT bank_name, account_number, holder
            FROM bank_accounts
            WHERE developer_id = ?
            """,
            (designer_id,)
        )
        data = await cursor.fetchone()

    if data is None:
        return False

    bank_name, account_number, holder = data
    embed = discord.Embed(
        title="💳 결제 정보",
        description=(
            f"🏦 {bank_name}\n"
            f"계좌번호 : `{account_number}`\n"
            f"예금주 : **{holder}**\n\n"
            "✅ 입금 후 담당 디자이너에게 말씀해주세요."
        ),
        color=discord.Color.green()
    )
    await channel.send(embed=embed)
    return True


@bot.command(name="계좌전송", aliases=["계좌번호", "결제정보"])
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

    await ctx.reply("✅ 결제 정보를 전송했습니다.", mention_author=False, delete_after=3)


@bot.command(name="티켓닫기", aliases=["닫기"])
async def close_ticket_by_command(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    closer = guild.get_member(ctx.author.id)

    if not can_manage_ticket(closer, ctx.author.id, designer_id):
        return await ctx.send("❌ 권한이 없습니다.")

    notice = await ctx.send("🔒 티켓 종료 처리 중입니다.")
    ticket_owner = await find_ticket_owner(channel)
    designer = await fetch_member_or_none(guild, designer_id)

    if designer:
        await delete_ticket_dm_messages(bot.user, designer, channel)

    await notice.edit(content="✅ 티켓 종료 완료. 아카이브로 이동합니다.")
    await asyncio.sleep(3)
    await archive_ticket_channel(channel)


@bot.command(name="티켓삭제", aliases=["삭제"])
async def delete_ticket_by_command(ctx):
    if not is_ticket_or_archive_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    deleter = guild.get_member(ctx.author.id)

    if not can_manage_ticket(deleter, ctx.author.id, designer_id):
        return await ctx.send("❌ 권한이 없습니다.")

    await ctx.send("🗑️ 티켓을 삭제합니다.")
    await asyncio.sleep(2)
    await delete_ticket_channel(channel, ctx.author)


# ==================== [자동 반복 태스크 및 시작 이벤트] ====================

@tasks.loop(minutes=30)
async def monthly_stats_updater():
    try:
        await update_monthly_stats_message(bot)
    except Exception as e:
        print(f"[월간 통계 갱신 실패] {e}")


@bot.event
async def setup_hook():
    if not os.getenv("OPENAI_API_KEY"):
        print("Auto translator disabled: OPENAI_API_KEY is missing")
        return
    await bot.load_extension("database.services.auto_translator")
    print("✅ 자동 번역 기능 로드 완료")


@bot.event
async def on_ready():
    global daily_notice, persistent_views_registered, update_notice_sent

    await create_tables()
    await init_ranking_db()

    print("on_ready")
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")

    if not persistent_views_registered:
        bot.add_view(TicketOpenView())
        bot.add_view(CustomCategoryView())
        bot.add_view(StarRatingView())
        bot.add_view(ProgressView())
        bot.add_view(PaymentView())
        bot.add_view(TicketCloseView())
        bot.add_view(VerifyView())
        persistent_views_registered = True

    if daily_notice is None:
        daily_notice = DailyNotice(bot)

    if not monthly_stats_updater.is_running():
        monthly_stats_updater.start()

    print("✨ 영속성 버튼 등록 완료!")


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
