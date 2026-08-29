import aiosqlite
from datetime import date
import discord
from discord.ext import commands
from database.database import DATABASE
from config import REGULAR_CUSTOMER_ROLE_ID, TARGET_REGULAR_POINTS

# ==========================================
# 1. 포인트 DB 처리 함수
# ==========================================

async def get_user_points(user_id: int) -> int:
    """유저의 현재 포인트 조회"""
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT points FROM user_points WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0

async def add_user_points(guild, member, amount: int) -> int:
    """포인트를 적립하고 기준 달성 시 단골 역할 자동 부여"""
    user_id = member.id
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id INTEGER PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_attendance_date TEXT
            )
        """)
        await db.execute("""
            INSERT INTO user_points (user_id, points) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET points = points + ?
        """, (user_id, amount, amount))
        await db.commit()
        
        cursor = await db.execute("SELECT points FROM user_points WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        new_points = row[0] if row else 0

    # 단골 역할 부여 체킹 (TARGET_REGULAR_POINTS 달성 시)
    if guild and member and new_points >= TARGET_REGULAR_POINTS:
        role = guild.get_role(REGULAR_CUSTOMER_ROLE_ID)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="단골 기준 포인트 달성")
                await member.send(
                    f"🎉 축하합니다! **{TARGET_REGULAR_POINTS:,} P**를 달성하여 **@{role.name}** 등급으로 승급하셨습니다!\n"
                    "앞으로 모든 커미션 이용 시 **15% 할인** 혜택이 자동 적용됩니다."
                )
            except Exception as e:
                print(f"[단골 역할 부여 실패] {e}")

    return new_points

async def process_daily_attendance(guild, member) -> tuple[bool, int, int]:
    """매일 1회 출석체크 처리 (+10P)"""
    today_str = date.today().isoformat()
    user_id = member.id
    
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id INTEGER PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_attendance_date TEXT
            )
        """)
        
        cursor = await db.execute("SELECT last_attendance_date FROM user_points WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        
        # 오늘 이미 출석체크를 완료한 경우
        if row and row[0] == today_str:
            current_points = await get_user_points(user_id)
            return False, 0, current_points

        # 출석 일자 업데이트
        await db.execute("""
            INSERT INTO user_points (user_id, points, last_attendance_date) VALUES (?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_attendance_date = ?
        """, (user_id, today_str, today_str))
        await db.commit()

    # 10 포인트 지급
    new_total = await add_user_points(guild, member, 10)
    return True, 10, new_total

async def add_review_points_by_bundle(guild, member, bundle_type: str = "단품 (1개)") -> tuple[int, int]:
    """후기 작성 시 묶음 종류에 따라 포인트를 차등 적립 (50P / 100P / 150P)"""
    if "2+1" in bundle_type:
        points_to_add = 100
    elif "3+1" in bundle_type:
        points_to_add = 150
    else:
        points_to_add = 50

    new_total = await add_user_points(guild, member, points_to_add)
    return points_to_add, new_total


# ==========================================
# 2. 디스코드 명령어 인터페이스 (Cog)
# ==========================================

class PointsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="출석", aliases=["출석체크", "출체"])
    async def attendance_cmd(self, ctx):
        """매일 1회 출석체크 명령어"""
        success, added_points, total_points = await process_daily_attendance(ctx.guild, ctx.author)
        if success:
            await ctx.send(f"✅ **{ctx.author.display_name}**님, 출석체크 완료! **+{added_points}P**가 적립되었습니다. (현재: **{total_points:,}P**)")
        else:
            await ctx.send(f"⚠️ **{ctx.author.display_name}**님, 오늘은 이미 출석체크를 하셨습니다. 내일 다시 시도해주세요! (현재: **{total_points:,}P**)")

    @commands.command(name="포인트", aliases=["마일리지", "p"])
    async def check_points_cmd(self, ctx, member: discord.Member = None):
        """보유 포인트 확인 명령어"""
        target = member or ctx.author
        pts = await get_user_points(target.id)
        await ctx.send(f"🪙 **{target.display_name}**님의 현재 보유 포인트: **{pts:,}P**")

async def setup(bot):
    await bot.add_cog(PointsCog(bot))
