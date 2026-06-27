import aiosqlite

DATABASE = "data/dialian.db"


async def connect():
    return await aiosqlite.connect(DATABASE)


async def create_tables():
    async with aiosqlite.connect(DATABASE) as db:

        # 개발자 정보
        await db.execute("""
        CREATE TABLE IF NOT EXISTS developers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            specialty TEXT,
            bank TEXT,
            account TEXT,
            owner TEXT
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
            order_id INTEGER,
            customer_id INTEGER,
            rating INTEGER,
            review TEXT
        )
        """)

        await db.commit()
