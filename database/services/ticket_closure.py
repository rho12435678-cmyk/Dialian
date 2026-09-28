"""Idempotent ticket close claims shared by UI buttons and bot commands.

SQLite BEGIN IMMEDIATE serializes simultaneous button and command clicks.
A stale 'closing' claim can be retried after 15 minutes following a crash.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from database import database

STALE_AFTER = timedelta(minutes=15)
_legacy_guard = asyncio.Lock()
_legacy_inflight = set()


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
            "SELECT status, updated_at, progress, completed_at FROM commissions WHERE ticket_channel=?",
            (channel_id,),
        ) as cursor:
            record = await cursor.fetchone()
        if record is None:
            await db.rollback()
            async with _legacy_guard:
                if channel_id in _legacy_inflight:
                    return False
                _legacy_inflight.add(channel_id)
                return "legacy"
        status, updated_at, progress, completed_at = record
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
        if cursor.rowcount != 1:
            return False
        if status == 'closing':
            return 'completed' if progress == 100 or completed_at else 'in_progress'
        return status


async def finish_ticket_close(channel_id, *, path=None, previous_status='in_progress'):
    if previous_status == 'legacy':
        async with _legacy_guard:
            _legacy_inflight.discard(channel_id)
        return
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await db.execute(
            """UPDATE commissions SET status=?, updated_at=?
               WHERE ticket_channel=? AND status='closing'""",
            ('completed' if previous_status == 'completed' else 'closed', datetime.now(timezone.utc).isoformat(), channel_id),
        )
        await db.commit()


async def abort_ticket_close(channel_id, *, path=None, previous_status='in_progress'):
    """Reopen on a transient error; do not modify completed/other statuses."""
    if previous_status == 'legacy':
        async with _legacy_guard:
            _legacy_inflight.discard(channel_id)
        return
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await db.execute(
            """UPDATE commissions SET status=?, updated_at=?
               WHERE ticket_channel=? AND status='closing'""",
            (previous_status if previous_status != 'closing' else 'in_progress', datetime.now(timezone.utc).isoformat(), channel_id),
        )
        await db.commit()
