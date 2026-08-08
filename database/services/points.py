import aiosqlite
from datetime import date
from database.database import DATABASE
from config import REGULAR_CUSTOMER_ROLE_ID, TARGET_REGULAR_POINTS

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
                last_share_date TEXT,
                last_feedback_date TEXT,
                feedback_today_count INTEGER DEFAULT 0
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
    if guild and new_points >= TARGET_REGULAR_POINTS:
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

async def add_review_points_by_bundle(guild, member, bundle_type: str = "단품 (1개)") -> tuple[int, int]:
    """후기 작성 시 묶음 종류에 따라 포인트를 차등 적립합니다."""
    if "2+1" in bundle_type:
        points_to_add = 45
    elif "3+1" in bundle_type:
        points_to_add = 60
    else:
        points_to_add = 30

    new_total = await add_user_points(guild, member, points_to_add)
    return points_to_add, new_total

async def check_and_add_share_points(guild, member, message) -> bool:
    """작품공유 어뷰징 검사 (+15P)"""
    if not message.attachments or len(message.content.strip()) < 20:
        return False

    today_str = date.today().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT last_share_date FROM user_points WHERE user_id = ?", (member.id,))
        row = await cursor.fetchone()
        
        if row and row[0] == today_str:
            return False  # 오늘 이미 적립함

        await db.execute("""
            INSERT INTO user_points (user_id, points, last_share_date) VALUES (?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_share_date = ?
        """, (member.id, today_str, today_str))
        await db.commit()

    await add_user_points(guild, member, 15)
    return True

async def check_and_add_feedback_points(guild, member, message) -> bool:
    """피드백 반응 감지 적립 (+10P, 일 최대 2회)"""
    if len(message.content.strip()) < 40:
        return False

    today_str = date.today().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT last_feedback_date, feedback_today_count FROM user_points WHERE user_id = ?", (member.id,))
        row = await cursor.fetchone()

        last_date = row[0] if row else None
        count = row[1] if row and last_date == today_str else 0

        if count >= 2:
            return False  # 오늘 2회 초과

        new_count = count + 1
        await db.execute("""
            INSERT INTO user_points (user_id, points, last_feedback_date, feedback_today_count) 
            VALUES (?, 0, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET 
                last_feedback_date = ?,
                feedback_today_count = ?
        """, (member.id, today_str, new_count))
        await db.commit()

    await add_user_points(guild, member, 10)
    return True
