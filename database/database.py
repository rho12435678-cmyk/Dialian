import os
import aiosqlite

os.makedirs("data", exist_ok=True)

DATABASE = "data/dialian.db"

async def connect():
    return await aiosqlite.connect(DATABASE)


async def create_tables():
    async with aiosqlite.connect(DATABASE) as db:

        # 개발자 정보
        await db.execute("""
CREATE TABLE IF NOT EXISTS commissions(

id INTEGER PRIMARY KEY AUTOINCREMENT,

ticket_channel INTEGER,

customer_id INTEGER,

designer_id INTEGER,

category TEXT,

status TEXT,

progress INTEGER,

estimate_day INTEGER,

created_at TEXT

)
""")

        #개발자 계좌
        await db.execute("""
CREATE TABLE IF NOT EXISTS bank_accounts(

developer_id INTEGER PRIMARY KEY,

bank_name TEXT,

account_number TEXT,

holder TEXT

)
""")
        
        # 고객 정보
        await db.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0
        )
        """)

        # 주문
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

                # 후기
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            developer_id INTEGER,
            customer_id INTEGER,
            stars INTEGER,
            review TEXT,
            created_at TEXT
        )
        """)

        # 같은 명령어 메시지를 여러 봇 프로세스가 중복 처리하지 않도록 기록
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

        await db.execute("""
        DELETE FROM processed_commands
        WHERE created_at < datetime('now', '-7 days')
        """)

        await db.execute("""
        DELETE FROM processed_command_errors
        WHERE created_at < datetime('now', '-7 days')
        """)

        await db.commit()
