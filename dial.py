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
REGULAR_CUSTOMER_ROLE_NAME = "REGULAR CUSTOMER/단골 손님"

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


# ==================== [포인트 및 단골 손님 혜택 로직] ====================

async def check_daily_limit(user_id: int, action_type: str, limit: int = 3) -> bool:
    """오늘 해당 액션을 몇 번 수행했는지 확인 (하루 최대 N회 제한)"""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_point_logs (
                user_id INTEGER,
                action_type TEXT,
                action_date TEXT
            )
        """)
        async with db.execute("""
            SELECT COUNT(*) FROM daily_point_logs 
            WHERE user_id = ? AND action_type = ? AND action_date = ?
        """, (user_id, action_type, today)) as cursor:
            count = (await cursor.fetchone())[0]
            if count >= limit:
                return False

        await db.execute("""
            INSERT INTO daily_point_logs (user_id, action_type, action_date)
            VALUES (?, ?, ?)
        """, (user_id, action_type, today))
        await db.commit()
        return True


async def grant_points_and_check_role(guild: discord.Guild, user_id: int, points_to_add: int) -> int:
    """포인트를 추가하고 1000P 이상일 경우 단골 손님 역할 지급"""
    new_points = await add_user_points(user_id, points_to_add)
    
    if new_points >= 1000 and guild:
        member = guild.get_member(user_id)
        if member:
            role = discord.utils.get(guild.roles, name=REGULAR_CUSTOMER_ROLE_NAME)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role)
                except Exception as e:
                    print(f"[역할 지급 오류] {e}")
    return new_points


# ==================== [통계 평점 및 월간 통계] ====================

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

    if row:
        channel_id, message_id = row
        channel = bot_instance.get_channel(channel_id)
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                embed = await build_monthly_stats_embed(channel.guild)
                await message.edit(embed=embed)
            except Exception as e:
                print(f"[Monthly Stats 오류] {e}")


# ==================== [티켓/커미션 관련 코드] ====================

async def send_ticket_guides(channel: discord.TextChannel, user: discord.User, designer_id: int = None):
    designer_text = f"<@{designer_id}>" if designer_id else "미배정 (랜덤)"

    guide_embed = discord.Embed(
        title="📌 커미션 안내 사항",
        description=(
            f"👨‍💻 **담당 디자이너** : {designer_text}\n\n"
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

    ref_embed = discord.Embed(
        title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
        description=(
            f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
            "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 구체적으로 올려주세요!"
        ),
        color=0x5865F2
    )
    ref_embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")

    await channel.send(embeds=[guide_embed, ref_embed])

    if designer_id:
        try:
            guild = channel.guild
            designer = guild.get_member(designer_id) or await guild.fetch_member(designer_id)
            if designer:
                progress_embed = discord.Embed(
                    title=f"📌 [{guild.name}] 커미션 진행 관리",
                    description=(
                        f"🏷️ 티켓 채널 : {channel.mention} (`{channel.name}`)\n"
                        f"👤 고객 : {user.mention} (`{user.name}`)\n\n"
                        "📌 상태 : 🟢 상담중\n"
                        "📊 진행률 : 0%\n"
                        "⏰ 예상 완료 : 작업 시작 전"
                    ),
                    color=0x5865F2
                )
                await designer.send(embed=progress_embed, view=ProgressView(ticket_channel_id=channel.id))
                await designer.send("💳 **계좌 정보 전송**", view=PaymentView())
                await designer.send("🔒 **티켓 관리 및 종료**", view=TicketCloseView())
        except Exception as e:
            print(f"[디자이너 DM 전송 실패] {e}")


class CustomCommissionModal(ui.Modal):
    def __init__(self, category: str, bundle_type: str, bundle_count: int, designer_id: int = None):
        super().__init__(title=f"[{category}] 커미션 신청서 ({bundle_type})")
        self.category = category
        self.bundle_type = bundle_type
        self.bundle_count = bundle_count
        self.designer_id = designer_id

        if category != "복장":
            self.roblox_name = ui.TextInput(label="🎮 Roblox 닉네임 (입력 필수 X)", style=discord.TextStyle.short, placeholder="예: DIAL_DESIGN", required=False)
            self.add_item(self.roblox_name)
            self.gfx_genre = ui.TextInput(label="🎬 GFX 장르", style=discord.TextStyle.short, placeholder="예: 밀리터리 / 로블룩 / 카페", required=True)
            self.add_item(self.gfx_genre)

        self.details = ui.TextInput(label="📌 세부 요구사항", style=discord.TextStyle.paragraph, placeholder="상세한 스타일, 원하시는 구도 등을 적어주세요.", required=True)
        self.add_item(self.details)

        if self.bundle_type in ["2+1 묶음", "3+1 묶음"]:
            bonus_label = "🎁 3번째 작품 요구사항" if self.bundle_type == "2+1 묶음" else "🎁 4번째 작품 요구사항"
            self.fourth_details = ui.TextInput(label=bonus_label, style=discord.TextStyle.paragraph, placeholder="보너스로 제공받을 작품 요구사항을 적어주세요.", required=True)
            self.add_item(self.fourth_details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, attach_files=True, embed_links=True)
        }

        if self.designer_id:
            designer_member = guild.get_member(self.designer_id)
            if designer_member:
                overwrites[designer_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)

        ticket_channel = await guild.create_text_channel(name=channel_name, topic=str(interaction.user.id), overwrites=overwrites)

        embed = discord.Embed(title=f"📋 {self.category} 커미션 신청서 ({self.bundle_type})", color=0x57F287)
        if hasattr(self, "roblox_name"):
            embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_name.value, inline=False)
        if hasattr(self, "gfx_genre"):
            embed.add_field(name="🎬 GFX 장르", value=self.gfx_genre.value, inline=False)

        designer_mention = f"<@{self.designer_id}>" if self.designer_id else "미배정 (랜덤)"
        embed.add_field(name="👨‍💻 담당 디자이너", value=designer_mention, inline=False)
        embed.add_field(name="📌 요청 상세 내용", value=self.details.value, inline=False)
        
        if hasattr(self, "fourth_details"):
            embed.add_field(name="🎁 보너스 작품 요구사항", value=self.fourth_details.value, inline=False)

        await ticket_channel.send(content=interaction.user.mention, embed=embed)
        await send_ticket_guides(ticket_channel, interaction.user, self.designer_id)

        now = datetime.now().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                INSERT INTO commissions (ticket_channel, customer_id, designer_id, category, status, progress, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'in_progress', 0, ?, ?)
            """, (ticket_channel.id, interaction.user.id, self.designer_id, self.category, now, now))
            await db.commit()

        await interaction.followup.send(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)


class BundleSelectionView(ui.View):
    def __init__(self, category: str, designer_id: int = None):
        super().__init__(timeout=None)
        self.category = category
        self.designer_id = designer_id

    @ui.button(label="1개 (기본)", style=discord.ButtonStyle.secondary)
    async def single_item(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "1개", 1, self.designer_id))

    @ui.button(label="2+1 묶음 (2개 결제, 3개 수령)", style=discord.ButtonStyle.success)
    async def bundle_2_plus_1(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "2+1 묶음", 3, self.designer_id))

    @ui.button(label="3+1 묶음 (3개 결제, 4개 수령)", style=discord.ButtonStyle.primary)
    async def bundle_3_plus_1(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CustomCommissionModal(self.category, "3+1 묶음", 4, self.designer_id))


class TicketCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CategorySelect())


class CategorySelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="랜덤 배정", description="디자이너를 무작위로 배정받습니다.", emoji="🎲"),
            discord.SelectOption(label="담당 디자이너 지정", description="원하는 디자이너를 선택합니다.", emoji="🎨")
        ]
        super().__init__(placeholder="디자이너 배정 방식을 선택하세요.", min_values=1, max_values=1, options=options, custom_id="assign_method_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        method = self.values[0]

        if method == "담당 디자이너 지정":
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS designer_status (
                        user_id INTEGER PRIMARY KEY,
                        active INTEGER DEFAULT 0
                    )
                """)
                async with db.execute("SELECT user_id, active FROM designer_status WHERE active = 1") as cursor:
                    active_designers = await cursor.fetchall()
            if not active_designers:
                await interaction.followup.send("현재 활동 중인 디자이너가 없습니다. ❌", ephemeral=True)
                return
            await interaction.followup.send("🎨 **담당 디자이너를 선택해주세요.**", view=DesignerSelectView(active_designers), ephemeral=True)
        else:
            await interaction.followup.send("📂 **신청할 커미션 종류를 선택해주세요.**", view=CategorySelectView(designer_id=None), ephemeral=True)


class DesignerSelectView(ui.View):
    def __init__(self, designers):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect(designers))


class DesignerSelect(ui.Select):
    def __init__(self, designers):
        options = [discord.SelectOption(label=f"디자이너 ID: {d[0]}", value=str(d[0]), emoji="🖌️") for d in designers]
        super().__init__(placeholder="담당 디자이너를 선택하세요.", min_values=1, max_values=1, options=options[:25], custom_id="designer_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        designer_id = int(self.values[0])
        await interaction.followup.send(f"✅ 선택된 디자이너: <@{designer_id}>\n📂 **신청할 커미션 종류를 선택해주세요.**", view=CategorySelectView(designer_id=designer_id), ephemeral=True)


class CategorySelectView(ui.View):
    def __init__(self, designer_id: int = None):
        super().__init__(timeout=None)
        self.designer_id = designer_id

    @ui.button(label="GFX 커미션", style=discord.ButtonStyle.primary, emoji="🖼️")
    async def btn_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🖼️ **GFX 커미션 신청 수량을 선택하세요.**", view=BundleSelectionView("GFX", self.designer_id), ephemeral=True)

    @ui.button(label="게임 UI / 썸네일", style=discord.ButtonStyle.primary, emoji="🎮")
    async def btn_game_ui(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🎮 **게임 UI / 썸네일 커미션 신청 수량을 선택하세요.**", view=BundleSelectionView("게임 UI / 썸네일", self.designer_id), ephemeral=True)

    @ui.button(label="복장 커미션", style=discord.ButtonStyle.primary, emoji="👕")
    async def btn_uniform(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("👕 **복장 커미션 신청 수량을 선택하세요.**", view=BundleSelectionView("복장", self.designer_id), ephemeral=True)

    @ui.button(label="그룹 로고 / 홍보지", style=discord.ButtonStyle.primary, emoji="🏢")
    async def btn_group_logo(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🏢 **그룹 로고 / 홍보지 커미션 신청 수량을 선택하세요.**", view=BundleSelectionView("그룹 로고 / 홍보지", self.designer_id), ephemeral=True)


# ==================== [봇 이벤트 및 루프] ====================

@bot.event
async def on_ready():
    global persistent_views_registered, update_notice_sent
    print(f"✅ 봇이 로그인했습니다: {bot.user}")

    await create_tables()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS designer_status (
                user_id INTEGER PRIMARY KEY,
                active INTEGER DEFAULT 0
            )
        """)
        await db.commit()

    bot.add_view(TicketOpenView())
    bot.add_view(TicketCategoryView())
    bot.add_view(VerifyView())
    bot.add_view(PaymentView())
    bot.add_view(TicketCloseView())

    if not persistent_views_registered:
        persistent_views_registered = True

    global daily_notice
    if daily_notice is None:
        daily_notice = DailyNotice(bot)

    if not daily_notice.daily_notice.is_running():
        daily_notice.daily_notice.start()

    if not update_presence.is_running():
        update_presence.start()
    if not backup_database_task.is_running():
        backup_database_task.start()
    if not db_cleanup_task.is_running():
        db_cleanup_task.start()
    if not update_stats_panel_task.is_running():
        update_stats_panel_task.start()
    if not update_ranking_panel_task.is_running():
        update_ranking_panel_task.start()


@tasks.loop(seconds=60)
async def update_presence():
    active_count = 0
    try:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute("SELECT COUNT(*) FROM designer_status WHERE active = 1") as cursor:
                row = await cursor.fetchone()
                if row:
                    active_count = row[0]
    except Exception as e:
        print(f"상태 업데이트 중 DB 오류: {e}")

    version = get_bot_version()
    base_messages = [
        "Dial Design Studio",
        "문의는 티켓 생성",
        f"현재 {active_count}명의 디자이너 작업 중!",
        f"버전: {version}"
    ]
    activities = [discord.Game(name=msg) for msg in base_messages]
    await bot.change_presence(activity=random.choice(activities))


@tasks.loop(hours=24)
async def backup_database_task():
    await backup_database()


@tasks.loop(hours=24)
async def db_cleanup_task():
    try:
        async with aiosqlite.connect(DATABASE) as db:
            one_week_ago = (datetime.now() - timedelta(days=7)).timestamp()
            await db.execute("DELETE FROM processed_commands WHERE message_id < ?", (one_week_ago,))
            await db.execute("DELETE FROM processed_command_errors WHERE message_id < ?", (one_week_ago,))
            await db.commit()
    except Exception as e:
        print(f"중복 처리 기록 정리 오류: {e}")


@tasks.loop(minutes=30)
async def update_stats_panel_task():
    await update_monthly_stats_message(bot)


# ==================== [포인트 이벤트 처리 (이미지 안내 기준)] ====================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    channel_name = message.channel.name if hasattr(message.channel, "name") else ""

    if "작품공유" in channel_name or "share-portfolio" in channel_name:
        if message.attachments and len(message.content.strip()) >= 20:
            if await check_daily_limit(message.author.id, "share_portfolio", limit=3):
                pts = await grant_points_and_check_role(message.guild, message.author.id, 15)
                await message.add_reaction("💼")
                await message.reply(f"🎉 작품 공유 포인트 **+15P** 적립! (현재: **{pts}P**)")
            else:
                await message.reply("⚠️ 작품 공유 포인트는 하루 최대 3회까지만 적립 가능합니다.", delete_after=5)
        elif not message.attachments:
            await message.reply("📸 이미지를 함께 첨부해주세요!", delete_after=5)
        elif len(message.content.strip()) < 20:
            await message.reply("📝 내용은 20자 이상 작성해주셔야 포인트가 적립됩니다.", delete_after=5)

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel or not hasattr(channel, "name"):
        return

    if "피드백" in channel.name or "feedback" in channel.name:
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == payload.user_id:
                return

            if await check_daily_limit(payload.user_id, "feedback_reaction", limit=3):
                guild = bot.get_guild(payload.guild_id)
                pts = await grant_points_and_check_role(guild, payload.user_id, 10)
                
                user = await bot.fetch_user(payload.user_id)
                if user:
                    await user.send(f"🎉 피드백 반응 적립 완료! **+10P** (현재: **{pts}P**)")
        except Exception as e:
            print(f"[피드백 적립 오류] {e}")


# ==================== [명령어: 포인트 & 회원 관리] ====================

@bot.command(name="포인트", aliases=["마일리지", "p"])
async def check_user_points(ctx, member: discord.Member = None):
    target = member or ctx.author
    pts = await get_user_points(target.id)
    
    role = discord.utils.get(ctx.guild.roles, name=REGULAR_CUSTOMER_ROLE_NAME)
    has_role = "적용 완료 (15% 할인 가능)" if (role and role in target.roles) else "미적용 (1000P 필요)"

    embed = discord.Embed(title="💰 포인트 & 혜택 현황", color=0x00FF00)
    embed.add_field(name="👤 대상", value=target.mention, inline=False)
    embed.add_field(name="✨ 보유 포인트", value=f"**{pts} P**", inline=True)
    embed.add_field(name="👑 단골 손님 혜택", value=has_role, inline=True)
    await ctx.send(embed=embed)


@bot.command(name="포인트지급")
@commands.has_permissions(administrator=True)
async def admin_give_points(ctx, member: discord.Member, amount: int):
    pts = await grant_points_and_check_role(ctx.guild, member.id, amount)
    await ctx.send(f"✅ {member.mention}님에게 **{amount}P**를 지급하였습니다. (현재: **{pts}P**)")


@bot.command(name="포인트차감")
@commands.has_permissions(administrator=True)
async def admin_deduct_points(ctx, member: discord.Member, amount: int):
    pts = await add_user_points(member.id, -amount)
    await ctx.send(f"✅ {member.mention}님의 포인트를 **{amount}P** 차감하였습니다. (현재: **{pts}P**)")


@bot.command(name="포인트리셋")
@commands.has_permissions(administrator=True)
async def admin_reset_points(ctx, member: discord.Member):
    current = await get_user_points(member.id)
    await add_user_points(member.id, -current)
    await ctx.send(f"✅ {member.mention}님의 포인트를 **0P**로 초기화했습니다.")


# ==================== [명령어: 오락실 & 미니게임] ====================

@bot.command(name="뽑기", aliases=["가챠", "럭키드로우"])
async def mini_game_gacha(ctx):
    pts = await get_user_points(ctx.author.id)
    if pts < 20:
        await ctx.send("❌ 포인트가 부족합니다. (필요 포인트: 20P)")
        return

    await add_user_points(ctx.author.id, -20)
    
    rand = random.random()
    if rand < 0.02:
        reward = 360
        msg = "🎉 **대박 잭팟! 360P에 당첨되었습니다!** 🎰"
    elif rand < 0.30:
        reward = 50
        msg = "✨ **축하합니다! 50P에 당첨되었습니다!**"
    elif rand < 0.60:
        reward = 20
        msg = "👍 **본전! 20P를 획득했습니다.**"
    else:
        reward = 0
        msg = "💀 **꽝! 다음 기회에...**"

    if reward > 0:
        await grant_points_and_check_role(ctx.guild, ctx.author.id, reward)

    new_pts = await get_user_points(ctx.author.id)
    await ctx.send(f"{msg}\n(현재 보유 포인트: **{new_pts} P**)")


@bot.command(name="가위바위보")
async def mini_game_rps(ctx, choice: str = None, bet: int = None):
    if not choice or bet is None:
        await ctx.send("사용법: `!가위바위보 [가위/바위/보] [배팅포인트]` (최소 10P)")
        return

    if bet < 10:
        await ctx.send("❌ 최소 배팅 포인트는 10P 이상이어야 합니다.")
        return

    pts = await get_user_points(ctx.author.id)
    if pts < bet:
        await ctx.send("❌ 보유 포인트가 부족합니다.")
        return

    options = ["가위", "바위", "보"]
    if choice not in options:
        await ctx.send("❌ '가위', '바위', '보' 중 하나를 입력해주세요.")
        return

    bot_choice = random.choice(options)
    
    if choice == bot_choice:
        await ctx.send(f"🤝 봇: **{bot_choice}** | 무승부입니다! 배팅금액이 반환됩니다.")
        return

    win_map = {"가위": "보", "바위": "가위", "보": "바위"}
    if win_map[choice] == bot_choice:
        win_amt = int(bet * 1.95) - bet
        await grant_points_and_check_role(ctx.guild, ctx.author.id, win_amt)
        new_pts = await get_user_points(ctx.author.id)
        await ctx.send(f"🎉 봇: **{bot_choice}** | **승리했습니다!** (+{win_amt}P 적립 / 현재: **{new_pts}P**)")
    else:
        await add_user_points(ctx.author.id, -bet)
        new_pts = await get_user_points(ctx.author.id)
        await ctx.send(f"💀 봇: **{bot_choice}** | **패배했습니다.** (-{bet}P 차감 / 현재: **{new_pts}P**)")


@bot.command(name="묵찌빠")
async def mini_game_mjb(ctx, choice: str = None, bet: int = None):
    if not choice or bet is None:
        await ctx.send("사용법: `!묵찌빠 [가위/바위/보] [배팅포인트]` (최소 10P)")
        return

    if bet < 10:
        await ctx.send("❌ 최소 배팅 포인트는 10P 이상이어야 합니다.")
        return

    pts = await get_user_points(ctx.author.id)
    if pts < bet:
        await ctx.send("❌ 보유 포인트가 부족합니다.")
        return

    options = ["가위", "바위", "보"]
    if choice not in options:
        await ctx.send("❌ '가위', '바위', '보' 중 하나를 입력해주세요.")
        return

    bot_choice = random.choice(options)
    
    if choice == bot_choice:
        await ctx.send(f"🤝 봇: **{bot_choice}** | 비겼습니다! 배팅금액이 반환됩니다.")
        return

    win_map = {"가위": "보", "바위": "가위", "보": "바위"}
    if win_map[choice] == bot_choice:
        win_amt = int(bet * 1.3) - bet
        await grant_points_and_check_role(ctx.guild, ctx.author.id, win_amt)
        new_pts = await get_user_points(ctx.author.id)
        await ctx.send(f"🎉 봇: **{bot_choice}** | **승리했습니다!** (+{win_amt}P 적립 / 현재: **{new_pts}P**)")
    else:
        await add_user_points(ctx.author.id, -bet)
        new_pts = await get_user_points(ctx.author.id)
        await ctx.send(f"💀 봇: **{bot_choice}** | **패배했습니다.** (-{bet}P 차감 / 현재: **{new_pts}P**)")


# ==================== [명령어: 명예 및 랭킹] ====================

async def build_ranking_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 Dial Design Studio 포인트 랭킹",
        description="현재 가장 많은 포인트를 보유하신 고객님들입니다!\n(주기적으로 갱신됩니다)",
        color=0xFFD700,
        timestamp=datetime.now()
    )
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT user_id, points FROM user_points ORDER BY points DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        embed.add_field(name="랭킹 정보 없음", value="아직 포인트를 획득한 고객이 없습니다.")
        return embed

    for idx, (uid, pts) in enumerate(rows, start=1):
        member = guild.get_member(uid)
        name = member.display_name if member else f"알 수 없음 (ID: {uid})"
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "🏅"
        embed.add_field(name=f"{medal} {idx}위: {name}", value=f"✨ **{pts} P**", inline=False)
    return embed


async def setup_ranking_panel(guild: discord.Guild):
    channel = guild.get_channel(POINT_RANKING_CHANNEL_ID)
    if not channel:
        return

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ranking_panel (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER,
                message_id INTEGER
            )
        """)
        cursor = await db.execute("SELECT message_id FROM ranking_panel WHERE id = 1")
        row = await cursor.fetchone()
        
    embed = await build_ranking_embed(guild)
    
    if row:
        try:
            msg = await channel.fetch_message(row[0])
            await msg.edit(embed=embed)
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO ranking_panel (id, channel_id, message_id)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET channel_id = excluded.channel_id, message_id = excluded.message_id
        """, (channel.id, new_msg.id))
        await db.commit()


@tasks.loop(minutes=30)
async def update_ranking_panel_task():
    for guild in bot.guilds:
        await setup_ranking_panel(guild)
        break


@bot.command(name="포인트랭킹")
@commands.has_permissions(administrator=True)
async def setup_ranking_cmd(ctx):
    await setup_ranking_panel(ctx.guild)
    await ctx.send("✅ 이 채널에 포인트 랭킹 패널이 생성(또는 갱신)되었습니다.", delete_after=5)
    await ctx.message.delete()


# ==================== [명령어: 진행 상황 및 작업 관리] ====================

@bot.command(name="진행")
async def cmd_progress(ctx, percent: int):
    if percent not in [0, 25, 50, 75, 100]:
        await ctx.send("❌ 진행률은 0, 25, 50, 75, 100 중 하나만 입력 가능합니다.")
        return

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE commissions SET progress = ? WHERE ticket_channel = ?", (percent, ctx.channel.id))
        await db.commit()
    
    embed = discord.Embed(title="📊 작업 진행률 업데이트", description=f"현재 작업 진행률이 **{percent}%**로 변경되었습니다.", color=0x3498DB)
    await ctx.send(embed=embed)


@bot.command(name="예상")
async def cmd_estimate(ctx, *, time_str: str):
    embed = discord.Embed(title="⏰ 예상 완료일 안내", description=f"고객님, 예상 작업 소요 시간은 **{time_str}** 입니다.", color=0xF1C40F)
    await ctx.send(embed=embed)


@bot.command(name="완료")
async def cmd_complete(ctx):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE commissions SET status = 'completed', progress = 100 WHERE ticket_channel = ?", (ctx.channel.id,))
        await db.commit()
    
    embed = discord.Embed(
        title="🎉 작업 완료 안내", 
        description="커미션 작업이 완료되었습니다! 최종 결과물을 확인해주세요.\n문제가 없다면 `!티켓닫기`를 통해 종료할 수 있습니다.", 
        color=0x2ECC71
    )
    await ctx.send(embed=embed)


# ==================== [명령어: 티켓 및 서버 관리] ====================

@bot.command(name="셋업")
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    embed = discord.Embed(
        title="📩 커미션 문의 / 티켓 생성",
        description="커미션 신청, 디자인 문의, 가격 상담 등을 위해 아래 버튼을 눌러주세요.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=TicketOpenView())


@bot.command(name="청소")
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("❌ 1에서 100 사이의 숫자를 입력해주세요.", delete_after=3)
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 **{len(deleted)-1}**개의 메시지를 청소했습니다.", delete_after=3)


@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def cmd_ticket_open(ctx):
    embed = discord.Embed(
        title="📩 커미션 문의 / 티켓 생성",
        description="커미션 신청, 디자인 문의, 가격 상담 등을 위해 아래 버튼을 눌러주세요.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=TicketOpenView())
    await ctx.message.delete()


@bot.command(name="인증패널")
@commands.has_permissions(administrator=True)
async def cmd_verify_panel(ctx):
    embed = discord.Embed(
        title="✅ 역할 인증",
        description="아래 버튼을 눌러 인증을 완료해주세요.",
        color=0x57F287
    )
    await ctx.send(embed=embed, view=VerifyView())
    await ctx.message.delete()


@bot.command(name="계좌전송")
async def cmd_payment_panel(ctx):
    await ctx.send("💳 **계좌 정보 전송**", view=PaymentView())
    await ctx.message.delete()


@bot.command(name="티켓닫기")
async def cmd_ticket_close(ctx):
    await ctx.send("🔒 **티켓 관리 및 종료**", view=TicketCloseView())
    await ctx.message.delete()


@bot.command(name="티켓삭제")
@commands.has_permissions(manage_channels=True)
async def cmd_ticket_delete(ctx):
    await ctx.send("🗑️ 5초 후 이 티켓 채널이 삭제됩니다...")
    await asyncio.sleep(5)
    await ctx.channel.delete()


# ==================== [시스템 구동] ====================

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ 환경 변수 파일(.env)에서 'TOKEN'을 찾을 수 없습니다.")
