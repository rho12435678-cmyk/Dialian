"""Idempotent ticket close claims shared by UI buttons and bot commands.

SQLite BEGIN IMMEDIATE serializes simultaneous button and command clicks.
A stale 'closing' claim can be retried after 15 minutes following a crash.
"""
from datetime import datetime, timedelta, timezone

import aiosqlite

from database import database

STALE_AFTER = timedelta(minutes=15)


def _timestamp(value):
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if (
            value and datetime.fromisoformat(value).tzinfo is None
        ) else datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


async def begin_ticket_close(channel_id, *, path=None, now=None):
    now = now or datetime.now(timezone.utc)
    path = path or database.DATABASE
    async with aiosqlite.connect(path, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT status, updated_at FROM commissions WHERE ticket_channel=?",
            (channel_id,),
        ) as cursor:
            record = await cursor.fetchone()
        if record is None:
            await db.rollback()
            return False
        status, updated_at = record
        if status in ("closed", "cancelled"):
            await db.rollback()
            return False
        if status == "closing":
            started = _timestamp(updated_at)
            if started is None or (now - started) < STALE_AFTER:
                await db.rollback()
                return False
        cursor = await db.execute(
            """UPDATE commissions SET status='closing', updated_at=?
               WHERE ticket_channel=?""",
            (now.isoformat(), channel_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def finish_ticket_close(channel_id, *, path=None):
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await db.execute(
            """UPDATE commissions SET status='closed', updated_at=?
               WHERE ticket_channel=? AND status='closing'""",
            (datetime.now(timezone.utc).isoformat(), channel_id),
        )
        await db.commit()


async def abort_ticket_close(channel_id, *, path=None):
    """Reopen on a transient error; do not modify completed/other statuses."""
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await db.execute(
            """UPDATE commissions SET status='in_progress', updated_at=?
               WHERE ticket_channel=? AND status='closing'""",
            (datetime.now(timezone.utc).isoformat(), channel_id),
        )
        await db.commit()
