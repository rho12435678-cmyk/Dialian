"""Deduplicated operator-only alerts. No customer text, IDs or secrets are sent."""
from datetime import datetime, timezone

import aiosqlite
import discord
from config import SECURITY_LOG_CHANNEL_ID
from database import database
from database.services.ops_health import record_failure

COOLDOWN_SECONDS = 3600


async def collect_alerts(*, path=None):
    path = path or database.DATABASE
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS ops_health_events(
                 id INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT NOT NULL,
                 error_type TEXT NOT NULL,
                 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
        )
        async with db.execute(
            """SELECT
              (SELECT COUNT(*) FROM review_point_awards
               WHERE status='pending' AND datetime(created_at) < datetime('now','-30 minutes')),
              (SELECT COUNT(*) FROM review_point_awards
               WHERE status='unpublished' AND datetime(created_at) < datetime('now','-30 minutes')),
              (SELECT COUNT(*) FROM ops_health_events
               WHERE datetime(created_at) >= datetime('now','-1 hour')),
              (SELECT COUNT(*) FROM commissions WHERE status='closing'
               AND datetime(updated_at) < datetime('now','-15 minutes'))"""
        ) as cursor:
            old_pending, old_unpublished, errors, stale_closing = await cursor.fetchone()
    alerts = {}
    if old_pending:
        alerts["review_pending"] = f"30분 이상 대기 중인 후기 포인트: {old_pending}건"
    if old_unpublished:
        alerts["review_unpublished"] = f"30분 이상 게시 확인이 필요한 후기: {old_unpublished}건"
    if errors >= 5:
        alerts["repeated_errors"] = f"최근 1시간에 오류 {errors}건 발생"
    if stale_closing:
        alerts["stale_closing"] = f"15분 이상 종료 처리 중인 티켓: {stale_closing}건"
    return alerts


async def claim_alert(key, *, path=None, now=None):
    """Persist cooldown before sending to prevent simultaneous duplicate alerts."""
    timestamp = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    async with aiosqlite.connect(path or database.DATABASE, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """CREATE TABLE IF NOT EXISTS ops_alert_cooldown(
                 alert_key TEXT PRIMARY KEY, sent_at INTEGER NOT NULL)"""
        )
        async with db.execute(
            "SELECT sent_at FROM ops_alert_cooldown WHERE alert_key=?", (key,)
        ) as cursor:
            prior = await cursor.fetchone()
        if prior and timestamp - prior[0] < COOLDOWN_SECONDS:
            await db.rollback()
            return False
        await db.execute(
            """INSERT INTO ops_alert_cooldown(alert_key, sent_at) VALUES(?,?)
               ON CONFLICT(alert_key) DO UPDATE SET sent_at=excluded.sent_at""",
            (key, timestamp),
        )
        await db.commit()
    return True


async def release_alert(key, *, path=None):
    async with aiosqlite.connect(path or database.DATABASE) as db:
        await db.execute("DELETE FROM ops_alert_cooldown WHERE alert_key=?", (key,))
        await db.commit()


async def send_operational_alerts(bot, guild, *, path=None):
    channel = guild.get_channel(SECURITY_LOG_CHANNEL_ID)
    if channel is None:
        return 0
    alerts = await collect_alerts(path=path)
    sent = 0
    for key, description in alerts.items():
        if not await claim_alert(key, path=path):
            continue
        try:
            embed = discord.Embed(
                title="⚠️ Dialian 운영 점검 필요",
                description=description + "\n관리자는 `!운영상태`에서 확인해 주세요.",
                color=discord.Color.orange(),
            )
            await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none(),
            )
            sent += 1
        except (discord.HTTPException, discord.Forbidden) as exc:
            # A definite API rejection permits retry. After an ambiguous crash,
            # cooldown remains, prioritizing no duplicate alerts.
            await release_alert(key, path=path)
            await record_failure("ops_alert", exc, path=path)
    return sent
