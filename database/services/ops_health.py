"""Persistent, bounded, sanitized operational diagnostics; no user payloads."""
import logging
import re

import aiosqlite
from database import database

logger = logging.getLogger("dialian.ops")
ALLOWED_COMPONENTS = frozenset({
    "review_repair", "review_notice", "ticket_close", "ticket_delete",
    "scheduled_backup", "monthly_stats",
})
_SECRET = re.compile(r"\b(?:\d[ -]?){10,20}\b|\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")


async def init_health_table(db):
    await db.execute(
        """CREATE TABLE IF NOT EXISTS ops_health_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component TEXT NOT NULL,
            error_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )


async def record_failure(component, exception, *, path=None):
    """Keep only approved component and exception CLASS; never log secrets/messages."""
    safe_component = component if component in ALLOWED_COMPONENTS else "unknown"
    error_type = type(exception).__name__[:64]
    # Do not log exception str(): it may contain private customer content.
    logger.warning("Dialian %s failure: %s", safe_component, error_type)
    try:
        async with aiosqlite.connect(path or database.DATABASE) as db:
            await init_health_table(db)
            await db.execute(
                "INSERT INTO ops_health_events(component,error_type) VALUES(?,?)",
                (safe_component, error_type),
            )
            # Retain only the newest 500 diagnostics (bounded DB growth).
            await db.execute(
                "DELETE FROM ops_health_events WHERE id NOT IN "
                "(SELECT id FROM ops_health_events ORDER BY id DESC LIMIT 500)"
            )
            await db.commit()
    except (aiosqlite.Error, OSError):
        logger.warning("Dialian operational event could not be stored")


async def health_snapshot(*, path=None):
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await init_health_table(db)
        async with db.execute(
            """SELECT
               (SELECT COUNT(*) FROM commissions WHERE status IN ('in_progress', 'pending')),
               (SELECT COUNT(*) FROM review_point_awards WHERE status='pending'),
               (SELECT COUNT(*) FROM review_point_awards WHERE status='unpublished'),
               (SELECT COUNT(*) FROM ops_health_events
                 WHERE created_at >= datetime('now', '-24 hours'))"""
        ) as cursor:
            active, pending, unpublished, recent_errors = await cursor.fetchone()
        async with db.execute(
            """SELECT component, COUNT(*) FROM ops_health_events
               WHERE created_at >= datetime('now', '-24 hours')
               GROUP BY component ORDER BY COUNT(*) DESC LIMIT 5"""
        ) as cursor:
            breakdown = await cursor.fetchall()
    return {
        "active_tickets": active,
        "pending_reviews": pending,
        "unpublished_reviews": unpublished,
        "errors_24h": recent_errors,
        "error_components": breakdown,
    }
