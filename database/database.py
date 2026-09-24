import os
import aiosqlite
from datetime import datetime

os.makedirs("data", exist_ok=True)

DATABASE = "data/dialian.db"


def secure_database_file():
    """DB 파일 및 폴더의 접근 권한을 보안 등급(700/600)으로 제한합니다."""
    try:
        os.chmod("data", 0o700)
        if os.path.exists(DATABASE):
            os.chmod(DATABASE, 0o600)
    except OSError:
        pass


async def connect():
    """aiosqlite 커넥션 객체를 반환합니다."""
    return await aiosqlite.connect(DATABASE)


async def create_tables():
    """모든 시스템 테이블 생성 및 스키마 마이그레이션을 수행합니다."""
    from database.services.roblox_verification import create_verification_tables

    async with aiosqlite.connect(DATABASE) as db:
        await create_verification_tables(db)

        # 1. 커미션 및 지원 티켓
        await db.execute("""
        CREATE TABLE IF NOT EXISTS commissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_channel INTEGER,
            customer_id INTEGER,
            designer_id INTEGER,
            designer_name TEXT,
            category TEXT,
            status TEXT,
            progress INTEGER DEFAULT 0,
            estimate_day INTEGER,
            created_at TEXT,
            completed_at TEXT,
            updated_at TEXT
        )
        """)

        await db.execute("""
        DELETE FROM commissions
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM commissions
            WHERE ticket_channel IS NOT NULL
            GROUP BY ticket_channel
        )
        AND ticket_channel IS NOT NULL
        """)

        await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_commissions_ticket_channel
        ON commissions(ticket_channel)
        """)

        for column_sql in (
            "ALTER TABLE commissions ADD COLUMN completed_at TEXT",
            "ALTER TABLE commissions ADD COLUMN updated_at TEXT",
            "ALTER TABLE commissions ADD COLUMN designer_name TEXT",
        ):
            try:
                await db.execute(column_sql)
            except aiosqlite.OperationalError:
                pass

        # 2. 월간 통계 패널
        await db.execute("""
        CREATE TABLE IF NOT EXISTS monthly_stats_panel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            channel_id INTEGER,
            message_id INTEGER
        )
        """)

        for col_sql in (
            "ALTER TABLE monthly_stats_panel ADD COLUMN id INTEGER",
            "ALTER TABLE monthly_stats_panel ADD COLUMN guild_id INTEGER",
            "ALTER TABLE monthly_stats_panel ADD COLUMN channel_id INTEGER",
            "ALTER TABLE monthly_stats_panel ADD COLUMN message_id INTEGER",
        ):
            try:
                await db.execute(col_sql)
            except aiosqlite.OperationalError:
                pass

        # 3. 디자이너 계좌 정보
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bank_accounts(
            developer_id INTEGER PRIMARY KEY,
            bank_name TEXT,
            account_number TEXT,
            holder TEXT
        )
        """)
        
        # 4. 고객 및 주문 정보
        await db.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            commission_type TEXT,
            designer_id INTEGER,
            status TEXT,
            progress INTEGER,
            estimated_days INTEGER,
            created_at TEXT
        )
        """)

        # 5. 후기 및 평점
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_channel INTEGER,
            developer_id INTEGER,
            customer_id INTEGER,
            stars INTEGER,
            review TEXT,
            created_at TEXT
        )
        """)

        try:
            await db.execute("ALTER TABLE reviews ADD COLUMN ticket_channel INTEGER")
        except aiosqlite.OperationalError:
            pass

        await db.execute("""
        DELETE FROM reviews
        WHERE ticket_channel IS NOT NULL
          AND rowid NOT IN (
              SELECT MIN(rowid)
              FROM reviews
              WHERE ticket_channel IS NOT NULL
              GROUP BY ticket_channel
          )
        """)
        await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_ticket_channel
        ON reviews(ticket_channel)
        """)

        # Existing reviews have no reliable historical payout ledger. Mark them
        # as unverified for administrator reconciliation, never grant again blindly.
        await db.execute("""
        CREATE TABLE IF NOT EXISTS review_point_awards (
            ticket_channel INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            awarded_at TEXT,
            approved_by INTEGER
        )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO review_point_awards
                (ticket_channel, customer_id, amount, status, created_at)
            SELECT ticket_channel, customer_id, 0, 'legacy_unverified',
                   COALESCE(created_at, CURRENT_TIMESTAMP)
            FROM reviews
            WHERE ticket_channel IS NOT NULL AND customer_id IS NOT NULL
        """)

        # 6. 유저 포인트 시스템
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0,
            last_share_date TEXT,
            last_feedback_date TEXT,
            feedback_today_count INTEGER DEFAULT 0,
            last_attendance_date TEXT,
            is_regular_notified INTEGER DEFAULT 0
        )
        """)

        for column_sql in (
            "ALTER TABLE user_points ADD COLUMN last_attendance_date TEXT",
            "ALTER TABLE user_points ADD COLUMN is_regular_notified INTEGER DEFAULT 0",
        ):
            try:
                await db.execute(column_sql)
            except aiosqlite.OperationalError:
                pass

        # 7. 패널 및 시스템 설정 (포인트 랭킹, 디자이너 등급 패널 등)
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
        CREATE TABLE IF NOT EXISTS daily_activity_limits (
            user_id INTEGER,
            action_type TEXT,
            date TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, action_type, date)
        )
        """)

        # 8. 블랙리스트
        await db.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            created_at TEXT
        )
        """)

        # 9. 중복 명령 및 시스템 로그
        await db.execute("""
        CREATE TABLE IF NOT EXISTS processed_commands (
            message_id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS processed_command_errors (
            message_id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 구버전 DB에는 created_at 컬럼이 없을 수 있으므로 정리 쿼리 전에 보강합니다.
        for table_name in ("processed_commands", "processed_command_errors"):
            try:
                await db.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN created_at TEXT"
                )
            except aiosqlite.OperationalError:
                pass
            await db.execute(
                f"""
                UPDATE {table_name}
                SET created_at = CURRENT_TIMESTAMP
                WHERE created_at IS NULL
                """
            )

        await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # 오래된 중복 명령 기록 정리 (7일 경과)
        await db.execute("""
        DELETE FROM processed_commands
        WHERE created_at < datetime('now', '-7 days')
        """)

        await db.execute("""
        DELETE FROM processed_command_errors
        WHERE created_at < datetime('now', '-7 days')
        """)

        await db.commit()
    secure_database_file()


# ==================== [🔒 블랙리스트 헬퍼 함수] ====================

async def is_blacklisted(user_id: int) -> bool:
    """유저가 블랙리스트에 등록되어 있는지 확인합니다."""
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row is not None


async def get_blacklist_info(user_id: int):
    """블랙리스트 유저의 차단 사유와 일시를 조회합니다."""
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute("SELECT reason, created_at FROM blacklist WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()


async def add_blacklist(user_id: int, reason: str = "사유 미기재"):
    """유저를 블랙리스트에 등록합니다."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO blacklist (user_id, reason, created_at) VALUES (?, ?, ?)",
            (user_id, reason, now)
        )
        await db.commit()


async def remove_blacklist(user_id: int):
    """유저를 블랙리스트에서 제거합니다."""
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        await db.commit()
