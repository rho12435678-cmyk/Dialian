"""Guild-scoped durable state. Legacy DDS schemas are rejected without mutation."""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @asynccontextmanager
    async def connect(self):
        async with aiosqlite.connect(self.path, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            yield db

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            existing = {row[0] for row in await (await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
            if {"commissions", "user_points", "family_subscriptions"} & existing:
                raise RuntimeError("DDS 데이터베이스는 DDH에 재사용할 수 없습니다. DDH_DATABASE에 별도 경로를 설정하세요.")
            await db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(guild_id, key)
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('developer','partner','feedback')),
                    fields TEXT NOT NULL, channel_id INTEGER UNIQUE,
                    state TEXT NOT NULL DEFAULT 'creating',
                    decision TEXT NOT NULL DEFAULT 'pending', reviewer_id INTEGER,
                    reason TEXT, close_reason TEXT, closed_by INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_kind
                    ON tickets(guild_id, owner_id, kind) WHERE state IN ('creating','open');
            """)
            await db.commit()
        if os.name == "posix":
            self.path.chmod(0o600)

    async def get(self, guild_id, key, default=None):
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key))).fetchone()
        return json.loads(row[0]) if row else default

    async def set(self, guild_id, key, value):
        async with self.connect() as db:
            await db.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(guild_id,key) "
                             "DO UPDATE SET value=excluded.value", (guild_id, key, json.dumps(value)))
            await db.commit()

    async def active(self, guild_id, owner_id, kind):
        async with self.connect() as db:
            row = await (await db.execute(
                "SELECT * FROM tickets WHERE guild_id=? AND owner_id=? AND kind=? "
                "AND state IN ('creating','open')", (guild_id, owner_id, kind))).fetchone()
        return dict(row) if row else None

    async def reserve(self, guild_id, owner_id, kind, fields):
        async with self.connect() as db:
            cursor = await db.execute(
                "INSERT INTO tickets(guild_id,owner_id,kind,fields,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (guild_id, owner_id, kind, json.dumps(fields, ensure_ascii=False), utcnow(), utcnow()))
            await db.commit()
            return await self.ticket(cursor.lastrowid, guild_id)

    async def ticket(self, ticket_id, guild_id):
        async with self.connect() as db:
            row = await (await db.execute("SELECT * FROM tickets WHERE id=? AND guild_id=?",
                                         (ticket_id, guild_id))).fetchone()
        return dict(row) if row else None

    async def channel_ticket(self, guild_id, channel_id):
        async with self.connect() as db:
            row = await (await db.execute("SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
                                         (guild_id, channel_id))).fetchone()
        return dict(row) if row else None

    async def tickets(self, guild_id, state):
        async with self.connect() as db:
            rows = await (await db.execute("SELECT * FROM tickets WHERE guild_id=? AND state=?",
                                          (guild_id, state))).fetchall()
        return [dict(row) for row in rows]

    async def approvals(self, guild_id):
        async with self.connect() as db:
            rows = await (await db.execute(
                "SELECT * FROM tickets WHERE guild_id=? AND state='open' AND decision='approving'",
                (guild_id,))).fetchall()
        return [dict(row) for row in rows]

    async def update_ticket(self, ticket_id, guild_id, **values):
        if not values or set(values) - {"channel_id", "state", "decision", "reviewer_id", "reason", "close_reason", "closed_by"}:
            raise ValueError("Invalid ticket update")
        values["updated_at"] = utcnow()
        columns = ",".join(f"{key}=?" for key in values)
        async with self.connect() as db:
            await db.execute(f"UPDATE tickets SET {columns} WHERE id=? AND guild_id=?",
                             (*values.values(), ticket_id, guild_id))
            await db.commit()
