"""Regression coverage for payment reassignment, single close, backup restore,
deep review scans and deduplicated operator alerts.
"""
import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from database import database
from database import backups
from database.views import payment_view
from database.services import ticket_access, ticket_closure, ops_alerts, ops_health, review_recovery


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "production.db")
        self.backup_dir = Path(tmp.name) / "backups"
        for module in (database, payment_view, backups):
            p = patch.object(module, "DATABASE", self.path)
            p.start()
            self.addCleanup(p.stop)
        await database.create_tables()

    async def add_ticket(self, ticket=700, assigned=333, status="in_progress"):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO commissions(ticket_channel,customer_id,designer_id,
                   category,status,updated_at) VALUES(?,111,?,'GFX',?,?)""",
                (ticket, assigned, status, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def test_old_payment_button_never_uses_cached_designer(self):
        await self.add_ticket()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bank_accounts VALUES(333,'Bank','1234','Account Holder')"
            )
            await db.commit()
        channel = SimpleNamespace(id=700, guild=SimpleNamespace(id=5), send=AsyncMock())
        view = payment_view.PaymentView(None, designer_id=222)
        button = next(item for item in view.children if item.custom_id == "ticket_payment")
        old = SimpleNamespace(
            guild=None, user=SimpleNamespace(id=222),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        new = SimpleNamespace(
            guild=None, user=SimpleNamespace(id=333),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        with patch.object(payment_view, "resolve_ticket_channel", new=AsyncMock(return_value=channel)):
            await button.callback(old)
            old.response.send_message.assert_awaited_once()
            channel.send.assert_not_awaited()
            await button.callback(new)
        channel.send.assert_awaited_once()
        self.assertIn("1234", channel.send.await_args.kwargs["embed"].description)

    async def test_payment_denied_for_closed_ticket(self):
        await self.add_ticket(status="closed")
        channel = SimpleNamespace(id=700, guild=SimpleNamespace(id=5), send=AsyncMock())
        view = payment_view.PaymentView(None, 333)
        button = next(item for item in view.children if item.custom_id == "ticket_payment")
        interaction = SimpleNamespace(guild=None,user=SimpleNamespace(id=333),
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(payment_view, "resolve_ticket_channel", new=AsyncMock(return_value=channel)):
            await button.callback(interaction)
        channel.send.assert_not_awaited()

    async def test_same_ticket_can_only_be_closed_once(self):
        await self.add_ticket()
        results = await asyncio.gather(*(
            ticket_closure.begin_ticket_close(700, path=self.path) for _ in range(3)
        ))
        self.assertEqual(sum(bool(result) for result in results), 1)
        await ticket_closure.finish_ticket_close(700, path=self.path)
        self.assertFalse(await ticket_closure.begin_ticket_close(700, path=self.path))

    async def test_failed_close_can_retry_and_completed_stays_completed(self):
        await self.add_ticket(status="completed")
        prior = await ticket_closure.begin_ticket_close(700, path=self.path)
        self.assertEqual(prior, "completed")
        await ticket_closure.abort_ticket_close(700, path=self.path, previous_status=prior)
        self.assertEqual(
            await ticket_closure.begin_ticket_close(700,path=self.path), "completed",
        )
        await ticket_closure.finish_ticket_close(
            700, path=self.path, previous_status="completed",
        )
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT status FROM commissions WHERE ticket_channel=700"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], "completed")

    async def test_stale_close_is_retryable_after_restart(self):
        await self.add_ticket(status="closing")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE commissions SET updated_at=? WHERE ticket_channel=700",
                ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),),
            )
            await db.commit()
        self.assertEqual(
            await ticket_closure.begin_ticket_close(700,path=self.path), "in_progress",
        )

    async def test_legacy_ticket_uses_single_inflight_close(self):
        # Pre-DB historical tickets still support the authorized close flow.
        first = await ticket_closure.begin_ticket_close(999, path=self.path)
        self.assertEqual(first, "legacy")
        self.assertFalse(await ticket_closure.begin_ticket_close(999, path=self.path))
        await ticket_closure.abort_ticket_close(
            999, path=self.path, previous_status=first,
        )
        again = await ticket_closure.begin_ticket_close(999, path=self.path)
        self.assertEqual(again, "legacy")
        await ticket_closure.finish_ticket_close(
            999, path=self.path, previous_status=again,
        )

    async def test_snapshot_restores_in_isolation_and_does_not_touch_live_db(self):
        await self.add_ticket()
        backup = await backups.backup_database(
            database_path=self.path, backup_dir=self.backup_dir,
        )
        self.assertTrue(backup.exists())
        self.assertTrue(backups.verify_backup_restore(backup))
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT COUNT(*) FROM commissions") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)
        broken = self.backup_dir / "corrupt.db"
        broken.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            backups.verify_backup_restore(broken)

    async def test_old_review_post_beyond_first_thousand_is_found(self):
        await self.add_ticket()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO review_point_awards(ticket_channel,customer_id,amount,status)
                   VALUES(700,111,50,'unpublished')"""
            )
            await db.commit()
        class ReviewChannel:
            def history(self, *, limit):
                self.requested_limit = limit
                async def messages():
                    for i in range(1200):
                        yield SimpleNamespace(id=i, author=SimpleNamespace(id=19), embeds=[])
                    yield SimpleNamespace(
                        id=555, author=SimpleNamespace(id=999),
                        embeds=[SimpleNamespace(
                            title="✨ 소중한 커미션 후기가 도착했습니다!",
                            footer=SimpleNamespace(text="DDS Review · Ticket ID: 700"),
                        )],
                    )
                return messages()
        channel = ReviewChannel()
        guild = SimpleNamespace(get_member=lambda x: discord.Object(id=x))
        credit = AsyncMock()
        await review_recovery.reconcile_review_awards(
            guild, 999, channel, credit, AsyncMock(), path=self.path,
        )
        self.assertGreater(channel.requested_limit, 1000)
        credit.assert_awaited_once()
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT status FROM review_point_awards WHERE ticket_channel=700"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], "pending")

    async def test_cooldown_deduplicates_private_operational_alerts(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO review_point_awards(ticket_channel,customer_id,amount,status,created_at)
                   VALUES(700,111,50,'unpublished',datetime('now','-40 minutes'))"""
            )
            await db.commit()
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(
            get_channel=lambda channel_id: channel
            if channel_id == ops_alerts.SECURITY_LOG_CHANNEL_ID else None,
        )
        first = await ops_alerts.send_operational_alerts(None, guild, path=self.path)
        second = await ops_alerts.send_operational_alerts(None, guild, path=self.path)
        self.assertEqual((first, second), (1, 0))
        channel.send.assert_awaited_once()
        self.assertFalse(channel.send.await_args.kwargs["allowed_mentions"].everyone)
        self.assertTrue(await ops_alerts.claim_alert(
            "review_unpublished",path=self.path,
            now=int(datetime.now(timezone.utc).timestamp()) + 3700,
        ))


if __name__ == "__main__":
    unittest.main()
