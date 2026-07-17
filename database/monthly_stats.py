from datetime import datetime, timedelta

import aiosqlite
import discord

from database.database import DATABASE


STATS_CHANNEL_KEY = "monthly_stats_channel_id"
STATS_MESSAGE_KEY = "monthly_stats_message_id"


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


async def update_monthly_stats_message(bot):
    channel_id = await get_setting(STATS_CHANNEL_KEY)
    message_id = await get_setting(STATS_MESSAGE_KEY)

    if not channel_id or not message_id:
        return False

    channel = bot.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return False

    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        return False

    embed = await build_monthly_stats_embed(channel.guild)
    await message.edit(embed=embed)
    return True
