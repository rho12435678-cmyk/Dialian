from datetime import datetime, timedelta
import re
import aiosqlite
import discord
from discord import ui

from database.database import DATABASE
from config import DESIGNER_ROLE_IDS

# ==========================================
# 1. 권한 및 헬퍼 함수
# ==========================================

STATS_CHANNEL_KEY = "monthly_stats_channel_id"
STATS_MESSAGE_KEY = "monthly_stats_message_id"
STATS_FOOTER_MARKER = "월간 통계"


def has_designer_role(member):
    if member is None:
        return False
    role_ids = {role_id for role_id in DESIGNER_ROLE_IDS.values() if role_id}
    return any(role.id in role_ids for role in member.roles)


def month_range(now=None):
    now = now or datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    return start, end


def iso(dt):
    return dt.isoformat()


async def set_setting(key, value):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO bot_settings(key, value)
            VALUES(?, ?)
            """,
            (key, str(value))
        )
        await db.commit()


async def get_setting(key):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            "SELECT value FROM bot_settings WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()

    return row[0] if row else None


def member_name(guild, member_id):
    if not member_id:
        return "미지정"

    member = guild.get_member(int(member_id))
    return member.display_name if member else str(member_id)


# ==========================================
# 2. ProgressView (디자이너 DM 진행률 뷰)
# ==========================================

class ProgressView(ui.View):
    # *args, **kwargs를 추가하여 외부에서 불필요한 인자(예: ticket_channel_id 등)가 전달되어도 에러가 나지 않도록 방어 코드 추가
    def __init__(self, designer_id: int = None, active_progress: int = 0, *args, **kwargs):
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
        
        match = re.search(r"<#(\d+)>", desc)
        if not match:
            return None, None
            
        channel_id = int(match.group(1))
        
        guild = message._state._get_client().guilds[0]
        for g in message._state._get_client().guilds:
            if g.get_channel(channel_id):
                guild = g
                break
                
        return channel_id, guild

    async def update_progress(self, interaction: discord.Interaction, progress: int, status: str, estimate: str):
        channel_id, guild = self.extract_channel_id_and_guild(interaction.message)
        if not channel_id or not guild:
            return await interaction.response.send_message(
                "❌ 이 패널에 연결된 티켓 채널 정보를 찾을 수 없습니다.", 
                ephemeral=True
            )

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
        
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT progress, status FROM commissions WHERE ticket_channel = ?", 
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                
        already_completed = row and row[0] == 100

        try:
            old_embed = interaction.message.embeds[0]
            new_embed = discord.Embed.from_dict(old_embed.to_dict())
            
            desc = new_embed.description or ""
            desc = re.sub(r"📌 상태 : .*", f"📌 상태 : {status}", desc)
            desc = re.sub(r"📊 진행률 : \d+%", f"📊 진행률 : {progress}%", desc)
            desc = re.sub(r"⏰ 예상 완료 : .*", f"⏰ 예상 완료 : {estimate}", desc)
            new_embed.description = desc

            new_view = ProgressView(designer_id=self.designer_id, active_progress=progress)
            await interaction.message.edit(embed=new_embed, view=new_view)
        except Exception as e:
            print(f"[DM 패널 갱신 오류] {e}")

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


# ==========================================
# 3. 월간 통계 관련 함수들
# ==========================================

async def build_monthly_stats_embed(guild):
    start, end = month_range()
    start_iso = iso(start)
    end_iso = iso(end)

    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM commissions
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso)
        )
        total_orders = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM commissions
            WHERE status = 'completed'
              AND completed_at >= ? AND completed_at < ?
            """,
            (start_iso, end_iso)
        )
        completed_orders = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM commissions
            WHERE status NOT IN ('completed', 'cancelled')
              AND created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso)
        )
        active_orders = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM commissions
            WHERE status = 'cancelled'
              AND updated_at >= ? AND updated_at < ?
            """,
            (start_iso, end_iso)
        )
        cancelled_orders = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            """
            SELECT COUNT(*), AVG(stars)
            FROM reviews
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso)
        )
        review_count, avg_rating = await cursor.fetchone()
        review_count = review_count or 0
        avg_rating = avg_rating or 0

        cursor = await db.execute(
            """
            SELECT COUNT(DISTINCT designer_id)
            FROM commissions
            WHERE designer_id IS NOT NULL
              AND created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso)
        )
        designer_count = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            """
            SELECT developer_id, AVG(stars) AS avg_stars
            FROM reviews
            WHERE created_at >= ? AND created_at < ?
            GROUP BY developer_id
            ORDER BY avg_stars DESC, COUNT(*) DESC
            LIMIT 1
            """,
            (start_iso, end_iso)
        )
        top_designer = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT designer_id, COUNT(*) AS completed_count
            FROM commissions
            WHERE status = 'completed'
              AND completed_at >= ? AND completed_at < ?
              AND designer_id IS NOT NULL
            GROUP BY designer_id
            ORDER BY completed_count DESC
            LIMIT 1
            """,
            (start_iso, end_iso)
        )
        most_active = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT created_at, completed_at
            FROM commissions
            WHERE status = 'completed'
              AND completed_at >= ? AND completed_at < ?
              AND created_at IS NOT NULL
              AND completed_at IS NOT NULL
            """,
            (start_iso, end_iso)
        )
        completed_rows = await cursor.fetchall()

        cursor = await db.execute(
            """
            SELECT COUNT(DISTINCT monthly.customer_id)
            FROM commissions AS monthly
            WHERE monthly.created_at >= ? AND monthly.created_at < ?
              AND monthly.customer_id IS NOT NULL
              AND (
                  SELECT COUNT(*)
                  FROM commissions AS total
                  WHERE total.customer_id = monthly.customer_id
              ) >= 2
            """,
            (start_iso, end_iso)
        )
        repeat_customers = (await cursor.fetchone())[0] or 0

    work_days = []

    for created_at, completed_at in completed_rows:
        try:
            created = datetime.fromisoformat(created_at)
            completed = datetime.fromisoformat(completed_at)
        except (TypeError, ValueError):
            continue

        seconds = max((completed - created).total_seconds(), 0)
        work_days.append(seconds / 86400)

    avg_work_days = sum(work_days) / len(work_days) if work_days else 0

    if top_designer:
        top_name = member_name(guild, top_designer[0])
        top_text = f"{top_name} ⭐{top_designer[1]:.2f}"
    else:
        top_text = "없음"

    if most_active:
        active_name = member_name(guild, most_active[0])
        active_text = f"{active_name} (완료 {most_active[1]}건)"
    else:
        active_text = "없음"

    description = (
        "```text\n"
        f"🗓️ {start.year}년 {start.month}월 {guild.name}\n\n"
        f"📦 총 주문 : {total_orders}\n"
        f"✅ 완료 : {completed_orders}\n"
        f"⌛ 진행 중 : {active_orders}\n"
        f"❌ 취소 : {cancelled_orders}\n"
        f"⭐ 평균 평점 : {avg_rating:.2f}\n"
        f"📝 후기 : {review_count}\n"
        f"👥 디자이너 : {designer_count}명\n"
        f"🔁 재주문 고객 : {repeat_customers}명\n\n"
        f"🏆 TOP Designer\n{top_text}\n\n"
        f"🔥 Most Active\n{active_text}\n\n"
        f"⏱️ 평균 작업기간\n{avg_work_days:.1f}일\n"
        "```"
    )

    embed = discord.Embed(
        description=description,
        color=discord.Color.dark_grey(),
        timestamp=datetime.now()
    )
    embed.set_footer(text="월간 통계는 주기적으로 자동 갱신됩니다.")
    return embed


async def save_monthly_stats_message(message):
    await set_setting(STATS_CHANNEL_KEY, message.channel.id)
    await set_setting(STATS_MESSAGE_KEY, message.id)


def is_monthly_stats_message(message, bot_user_id):
    if message.author.id != bot_user_id or not message.embeds:
        return False

    embed = message.embeds[0]
    footer = embed.footer.text or ""
    description = embed.description or ""
    return STATS_FOOTER_MARKER in footer or "총 주문" in description


async def find_existing_monthly_stats_message(bot):
    candidates = []

    for guild in bot.guilds:
        for channel in guild.text_channels:
            try:
                async for message in channel.history(limit=100, oldest_first=False):
                    if is_monthly_stats_message(message, bot.user.id):
                        candidates.append(message)
                        break
            except (discord.Forbidden, discord.HTTPException):
                continue

    if not candidates:
        return None

    message = max(candidates, key=lambda item: item.created_at)
    await save_monthly_stats_message(message)
    return message


async def update_monthly_stats_message(bot):
    channel_id = await get_setting(STATS_CHANNEL_KEY)
    message_id = await get_setting(STATS_MESSAGE_KEY)

    if not channel_id or not message_id:
        message = await find_existing_monthly_stats_message(bot)
        if message is None:
            return False
        channel_id = message.channel.id
        message_id = message.id

    channel = bot.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return False

    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        message = await find_existing_monthly_stats_message(bot)
        if message is None:
            return False

    embed = await build_monthly_stats_embed(message.channel.guild)
    await message.edit(embed=embed)
    await save_monthly_stats_message(message)
    return True
