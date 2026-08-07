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
            f"INSERT OR IGNORE INTO {table_name}(message_id) VALUES (?)",
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

    if row:
        channel_id, message_id = row
        channel = bot_instance.get_channel(channel_id)
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                embed = await build_monthly_stats_embed(channel.guild)
                await message.edit(embed=embed)
                print("[Monthly Stats] 월간 통계 패널 업데이트 성공")
            except discord.NotFound:
                print("[Monthly Stats] 월간 통계 메시지를 찾을 수 없습니다. (삭제됨)")
            except discord.Forbidden:
                print("[Monthly Stats] 월간 통계 메시지를 수정할 권한이 없습니다.")
            except Exception as e:
                print(f"[Monthly Stats] 월간 통계 메시지 수정 중 오류 발생: {e}")


# ==================== [티켓/커미션 관련 코드] ====================

async def send_ticket_guides(channel: discord.TextChannel, user: discord.User, designer_id: int = None):
    # 담당 디자이너 텍스트 처리
    if designer_id:
        designer_text = f"<@{designer_id}>"
    else:
        designer_text = "미배정 (랜덤)"

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

    # 1. 일반 티켓 채널에는 안내 및 참고 자료 임베드 전송
    await channel.send(embeds=[guide_embed, ref_embed])

    # 2. 담당 디자이너가 지정되어 있는 경우, 디자이너의 DM으로만 진행 패널 및 관리 버튼 전송
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
                await designer.send(
                    embed=progress_embed, 
                    view=ProgressView(ticket_channel_id=channel.id)
                )
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

        # 복장 커미션이 아닌 경우에만 roblox_name, gfx_genre 필드 추가
        if category != "복장":
            self.roblox_name = ui.TextInput(
                label="🎮 Roblox 닉네임 (입력 필수 X)",
                style=discord.TextStyle.short,
                placeholder="예: DIAL_DESIGN",
                required=False
            )
            self.add_item(self.roblox_name)

            self.gfx_genre = ui.TextInput(
                label="🎬 GFX 장르",
                style=discord.TextStyle.short,
                placeholder="예: 밀리터리 / 로블룩 / 게임 / 병맛 / 카페",
                required=True
            )
            self.add_item(self.gfx_genre)

        self.details = ui.TextInput(
            label="📌 세부 요구사항",
            style=discord.TextStyle.paragraph,
            placeholder="상세한 스타일, 원하시는 구도 등을 적어주세요. 묶음 신청 시 각 작품별로 설명해 주세요.",
            required=True
        )
        self.add_item(self.details)

        # 묶음 보너스 요구사항 필드 (2+1, 3+1 등)
        if self.bundle_type in ["2+1 묶음", "3+1 묶음"]:
            bonus_label = "🎁 3번째 작품 요구사항" if self.bundle_type == "2+1 묶음" else "🎁 4번째 작품 요구사항"
            self.fourth_details = ui.TextInput(
                label=bonus_label,
                style=discord.TextStyle.paragraph,
                placeholder="보너스로 제공받을 작품에 대한 요구사항을 적어주세요.",
                required=True
            )
            self.add_item(self.fourth_details)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel_name = f"티켓-{interaction.user.name}"
        
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
        
        # 닉네임이 있는 경우에만 임베드에 추가 (복장은 제외됨)
        if hasattr(self, "roblox_name"):
            embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_name.value, inline=False)
        
        if hasattr(self, "gfx_genre"):
            embed.add_field(name="🎬 GFX 장르", value=self.gfx_genre.value, inline=False)

        # 👨‍💻 담당 디자이너 항목 추가
        designer_mention = f"<@{self.designer_id}>" if self.designer_id else "미배정 (랜덤)"
        embed.add_field(name="👨‍💻 담당 디자이너", value=designer_mention, inline=False)
            
        embed.add_field(name="📌 요청 상세 내용", value=self.details.value, inline=False)
        
        if hasattr(self, "fourth_details"):
            bonus_label = "🎁 3번째 작품 요구사항 (2+1 보너스)" if self.bundle_type == "2+1 묶음" else "🎁 4번째 작품 요구사항 (3+1 보너스)"
            embed.add_field(name=bonus_label, value=self.fourth_details.value, inline=False)

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
                async with db.execute("SELECT user_id, active FROM designer_status WHERE active = 1") as cursor:
                    active_designers = await cursor.fetchall()
            if not active_designers:
                await interaction.followup.send("현재 활동 중인 디자이너가 없습니다. ❌", ephemeral=True)
                return
            await interaction.followup.send(
                "🎨 **담당 디자이너를 선택해주세요.**",
                view=DesignerSelectView(active_designers),
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "📂 **신청할 커미션 종류를 선택해주세요.**",
                view=CategorySelectView(designer_id=None),
                ephemeral=True
            )


class DesignerSelectView(ui.View):
    def __init__(self, designers):
        super().__init__(timeout=None)
        self.add_item(DesignerSelect(designers))


class DesignerSelect(ui.Select):
    def __init__(self, designers):
        options = []
        for d in designers:
            options.append(discord.SelectOption(label=f"디자이너 ID: {d[0]}", value=str(d[0]), emoji="🖌️"))
        super().__init__(placeholder="담당 디자이너를 선택하세요.", min_values=1, max_values=1, options=options[:25], custom_id="designer_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        designer_id = int(self.values[0])
        await interaction.followup.send(
            f"✅ 선택된 디자이너: <@{designer_id}>\n📂 **신청할 커미션 종류를 선택해주세요.**",
            view=CategorySelectView(designer_id=designer_id),
            ephemeral=True
        )


class CategorySelectView(ui.View):
    def __init__(self, designer_id: int = None):
        super().__init__(timeout=None)
        self.designer_id = designer_id
        
        # 버튼에 사용할 커스텀 ID를 추가하여 식별 가능하게 함
        self.btn_gfx.custom_id = f"cat_btn_gfx_{designer_id}"
        self.btn_game_ui.custom_id = f"cat_btn_game_ui_{designer_id}"
        self.btn_uniform.custom_id = f"cat_btn_uniform_{designer_id}"
        self.btn_group_logo.custom_id = f"cat_btn_group_logo_{designer_id}"

    @ui.button(label="GFX 커미션", style=discord.ButtonStyle.primary, emoji="🖼️")
    async def btn_gfx(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🖼️ **GFX 커미션 신청 수량(묶음)을 선택하세요.**", view=BundleSelectionView("GFX", self.designer_id), ephemeral=True)

    @ui.button(label="게임 UI / 썸네일", style=discord.ButtonStyle.primary, emoji="🎮")
    async def btn_game_ui(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🎮 **게임 UI / 썸네일 커미션 신청 수량(묶음)을 선택하세요.**", view=BundleSelectionView("게임 UI / 썸네일", self.designer_id), ephemeral=True)

    @ui.button(label="복장 커미션", style=discord.ButtonStyle.primary, emoji="👕")
    async def btn_uniform(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("👕 **복장 커미션 신청 수량(묶음)을 선택하세요.**", view=BundleSelectionView("복장", self.designer_id), ephemeral=True)

    @ui.button(label="그룹 로고 / 홍보지", style=discord.ButtonStyle.primary, emoji="🏢")
    async def btn_group_logo(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("🏢 **그룹 로고 / 홍보지 커미션 신청 수량(묶음)을 선택하세요.**", view=BundleSelectionView("그룹 로고 / 홍보지", self.designer_id), ephemeral=True)


# ==================== [봇 이벤트 및 루프] ====================

@bot.event
async def on_ready():
    global persistent_views_registered, update_notice_sent
    print(f"✅ 봇이 로그인했습니다: {bot.user}")

    await create_tables()
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCategoryView())
    bot.add_view(VerifyView())
    bot.add_view(PaymentView())
    bot.add_view(TicketCloseView())

    if not persistent_views_registered:
        print("[INFO] 영구 View(ProgressView, StarRatingView)가 등록되었습니다.")
        persistent_views_registered = True

    global daily_notice
    if daily_notice is None:
        daily_notice = DailyNotice(bot)

    if not daily_notice.send_daily_notice.is_running():
        daily_notice.send_daily_notice.start()

    update_presence.start()
    backup_database_task.start()
    db_cleanup_task.start()
    update_stats_panel_task.start()
    update_ranking_panel_task.start()

    bot_version = get_bot_version()
    bot_channel_id = int(os.getenv("BOT_LOG_CHANNEL_ID", "0"))
    if bot_channel_id != 0 and not update_notice_sent:
        channel = bot.get_channel(bot_channel_id)
        if channel:
            embed = discord.Embed(
                title="🔄 봇 업데이트 완료",
                description="새로운 변경 사항이 적용되어 봇이 다시 시작되었습니다.",
                color=0x57F287
            )
            embed.add_field(name="🕒 업데이트 시간", value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            embed.add_field(name="📦 버전 (Git HEAD)", value=f"`{bot_version}`")
            await channel.send(embed=embed)
            update_notice_sent = True


@bot.event
async def on_command_error(ctx, error):
    message_id = ctx.message.id
    try:
        await claim_once("processed_command_errors", message_id)
    except ValueError:
        return

    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"❌ '{ctx.invoked_with}'라는 명령어를 찾을 수 없습니다.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ 필수 항목이 누락되었습니다: {error.param.name}")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ 이 명령어를 사용할 권한이 없습니다.")
    else:
        await ctx.send("⚠️ 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        print(f"Error: {error}")


@tasks.loop(seconds=60)
async def update_presence():
    """
    봇 상태 메시지 순환 (디자이너 활동 상태 포함)
    """
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
        "친절한 상담 진행 중",
        "!도움말로 명령어 확인",
        f"현재 {active_count}명의 디자이너가 작업 중!",
        f"버전: {version}"
    ]
    activities = [discord.Game(name=msg) for msg in base_messages]
    await bot.change_presence(activity=random.choice(activities))


@tasks.loop(hours=24)
async def backup_database_task():
    backup_database()
    print("✅ 데이터베이스 백업 완료")


@tasks.loop(hours=24)
async def db_cleanup_task():
    try:
        async with aiosqlite.connect(DATABASE) as db:
            one_week_ago = (datetime.now() - timedelta(days=7)).timestamp()
            await db.execute("DELETE FROM processed_commands WHERE message_id < ?", (one_week_ago,))
            await db.execute("DELETE FROM processed_command_errors WHERE message_id < ?", (one_week_ago,))
            await db.commit()
        print("✅ 중복 처리 기록 정리 완료")
    except Exception as e:
        print(f"중복 처리 기록 정리 중 오류 발생: {e}")


@tasks.loop(minutes=30)
async def update_stats_panel_task():
    await update_monthly_stats_message(bot)


# ==================== [포인트 및 랭킹 패널 관련] ====================

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
        if idx == 1:
            medal = "🥇"
        elif idx == 2:
            medal = "🥈"
        elif idx == 3:
            medal = "🥉"
        else:
            medal = "🏅"
        embed.add_field(
            name=f"{medal} {idx}위: {name}",
            value=f"✨ **{pts} P**",
            inline=False
        )
    return embed


async def setup_ranking_panel(guild: discord.Guild):
    channel = guild.get_channel(POINT_RANKING_CHANNEL_ID)
    if not channel:
        print(f"[Ranking Panel] 지정된 랭킹 채널({POINT_RANKING_CHANNEL_ID})을 찾을 수 없습니다.")
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
        message_id = row[0]
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            print("[Ranking Panel] 랭킹 패널 업데이트 완료")
            return
        except discord.NotFound:
            print("[Ranking Panel] 기존 메시지를 찾을 수 없어 새로 생성합니다.")
        except Exception as e:
            print(f"[Ranking Panel] 업데이트 중 오류: {e}")

    # 새 메시지 생성
    new_msg = await channel.send(embed=embed)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO ranking_panel (id, channel_id, message_id)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
        """, (channel.id, new_msg.id))
        await db.commit()
    print("[Ranking Panel] 랭킹 패널 최초 생성 완료")


@tasks.loop(minutes=30)
async def update_ranking_panel_task():
    for guild in bot.guilds:
        await setup_ranking_panel(guild)
        break


# ==================== [명령어: 포인트 / 상태 관리] ====================

@bot.command(name="포인트조회")
async def check_points(ctx):
    pts = await get_user_points(ctx.author.id)
    embed = discord.Embed(
        title="💰 포인트 조회",
        description=f"{ctx.author.mention}님의 현재 포인트는 **{pts} P** 입니다.",
        color=0x00FF00
    )
    await ctx.send(embed=embed)


@bot.command(name="랭킹업데이트")
@commands.has_permissions(administrator=True)
async def manual_update_ranking(ctx):
    await setup_ranking_panel(ctx.guild)
    await ctx.send("✅ 랭킹 패널이 수동으로 업데이트되었습니다.")


@bot.command(name="활동시작")
async def start_designer_activity(ctx):
    if not has_designer_role(ctx.author, ctx.guild):
        await ctx.send("❌ 디자이너 권한이 없습니다.")
        return

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS designer_status (
                user_id INTEGER PRIMARY KEY,
                active INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            INSERT INTO designer_status (user_id, active)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET active = 1
        """, (ctx.author.id,))
        await db.commit()

    await ctx.send(f"✅ {ctx.author.mention}님, 활동을 시작하셨습니다! (상태: **ON**)")


@bot.command(name="활동종료")
async def stop_designer_activity(ctx):
    if not has_designer_role(ctx.author, ctx.guild):
        await ctx.send("❌ 디자이너 권한이 없습니다.")
        return

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            UPDATE designer_status SET active = 0 WHERE user_id = ?
        """, (ctx.author.id,))
        await db.commit()

    await ctx.send(f"💤 {ctx.author.mention}님, 활동을 종료하셨습니다! (상태: **OFF**)")


# ==================== [이벤트: 메시지 분석(후기/홍보 포인트 지급)] ====================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    channel_name = message.channel.name if hasattr(message.channel, "name") else ""
    
    # 1. 후기 게시판 포인트 지급 (사진 + 특정 글자수)
    if "후기" in channel_name or channel_name == "review":
        if message.attachments:
            if len(message.content) >= 10:
                added = await check_and_add_feedback_points(message.author.id, message.id)
                if added:
                    await message.add_reaction("⭐")
                    await message.reply(f"🎉 소중한 후기 감사합니다! 포인트(500P)가 적립되었습니다. (현재 포인트: {await get_user_points(message.author.id)} P)")
            else:
                await message.reply("📝 후기 내용은 10자 이상 작성해주셔야 포인트가 지급됩니다.")

    # 2. 홍보 인증 게시판 포인트 지급
    elif "홍보" in channel_name or channel_name == "promo":
        if message.attachments:
            added = await check_and_add_share_points(message.author.id, message.id)
            if added:
                await message.add_reaction("📣")
                await message.reply(f"🎉 홍보 인증 감사합니다! 포인트(300P)가 적립되었습니다. (현재 포인트: {await get_user_points(message.author.id)} P)")
        else:
            await message.reply("📸 홍보 인증 스크린샷(사진)을 함께 첨부해주셔야 포인트가 지급됩니다.")

    await bot.process_commands(message)


# ==================== [명령어: 관리자 및 패널 전용] ====================

@bot.command(name="셋업")
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    embed = discord.Embed(
        title="📩 커미션 문의 / 티켓 생성",
        description="커미션 신청, 디자인 문의, 가격 상담 등을 위해 아래 버튼을 눌러주세요.\n\n"
                    "👉 **커미션 진행 과정**\n"
                    "1️⃣ 티켓 생성 및 신청서 작성\n"
                    "2️⃣ 담당 디자이너 배정 및 안내\n"
                    "3️⃣ 결제 및 진행\n"
                    "4️⃣ 작업 완료 후 파일 전달 및 티켓 종료",
        color=0x5865F2
    )
    embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_footer(text="버튼을 누르면 개인 티켓 채널이 생성됩니다.")

    await ctx.send(embed=embed, view=TicketOpenView())


@bot.command(name="인증셋업")
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    embed = discord.Embed(
        title="✅ 역할 인증",
        description="서버의 모든 기능을 이용하시려면 아래 버튼을 눌러 인증을 완료해주세요.",
        color=0x57F287
    )
    await ctx.send(embed=embed, view=VerifyView())


@bot.command(name="월간통계셋업")
@commands.has_permissions(administrator=True)
async def setup_monthly_stats(ctx):
    embed = await build_monthly_stats_embed(ctx.guild)
    msg = await ctx.send(embed=embed)
    await save_monthly_stats_message(msg)
    await ctx.send("✅ 월간 통계 패널이 설정되었습니다. (자동으로 업데이트됩니다.)", delete_after=5)


@bot.command(name="백업")
@commands.has_permissions(administrator=True)
async def manual_backup(ctx):
    backup_database()
    await ctx.send("✅ 데이터베이스 수동 백업이 완료되었습니다.")


@bot.command(name="로그정리")
@commands.has_permissions(administrator=True)
async def cleanup_command_logs(ctx):
    try:
        async with aiosqlite.connect(DATABASE) as db:
            one_week_ago = (datetime.now() - timedelta(days=7)).timestamp()
            await db.execute("DELETE FROM processed_commands WHERE message_id < ?", (one_week_ago,))
            await db.execute("DELETE FROM processed_command_errors WHERE message_id < ?", (one_week_ago,))
            await db.commit()
        await ctx.send("✅ 일주일이 지난 명령어 처리 기록을 정리했습니다.")
    except Exception as e:
        await ctx.send(f"❌ 기록 정리 중 오류가 발생했습니다: {e}")


@bot.command(name="버전")
async def show_version(ctx):
    uptime = datetime.now() - bot_started_at
    version = get_bot_version()
    
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}시간 {minutes}분 {seconds}초"

    embed = discord.Embed(
        title="🤖 봇 정보",
        color=0x5865F2
    )
    embed.add_field(name="📦 버전 (Git HEAD)", value=f"`{version}`", inline=False)
    embed.add_field(name="⏱️ 가동 시간", value=uptime_str, inline=False)
    
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ 환경 변수 파일(.env)에서 'TOKEN'을 찾을 수 없습니다.")
