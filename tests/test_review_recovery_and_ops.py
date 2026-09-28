"""Recovery interruption and privacy-safe observability regressions."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from database import database
from database.services import ops_health, review_recovery, points


class ReviewRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "recovery.db")
        for module in (database, points):
            p = patch.object(module, "DATABASE", self.path)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(points, "refresh_point_ranking", new=AsyncMock())
        p.start()
        self.addCleanup(p.stop)
        await database.create_tables()
        self.guild = SimpleNamespace(get_member=lambda user_id: discord.Object(id=user_id))
        self.refresh = AsyncMock()

    async def add_award(self, ticket=700, status="unpublished", customer=111, amount=50):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO review_point_awards(ticket_channel,customer_id,amount,status) "
                "VALUES(?,?,?,?)",
                (ticket, customer, amount, status),
            )
            await db.commit()

    async def stored_status(self, ticket=700):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT status FROM review_point_awards WHERE ticket_channel=?",
                (ticket,),
            ) as cursor:
                return (await cursor.fetchone())[0]

    @staticmethod
    def review_message(*, author=999, ticket=700, title="✨ 소중한 커미션 후기가 도착했습니다!"):
        return SimpleNamespace(
            id=555,
            author=SimpleNamespace(id=author),
            embeds=[SimpleNamespace(
                title=title,
                footer=SimpleNamespace(text=f"DDS Review · Ticket ID: {ticket}"),
            )],
        )

    @staticmethod
    def channel(messages):
        class FakeReviewChannel:
            def history(self, **kwargs):
                async def iterate():
                    for message in messages:
                        yield message
                return iterate()
        return FakeReviewChannel()

    async def recover(self, channel=None, credit=None):
        return await review_recovery.reconcile_review_awards(
            self.guild, 999, channel,
            credit or points.credit_review_award, self.refresh, path=self.path,
        )

    async def test_unconfirmed_or_forged_posts_never_release_credit(self):
        await self.add_award()
        await self.recover(self.channel([self.review_message(author=123)]))
        self.assertEqual(await self.stored_status(), "unpublished")
        self.assertEqual(await points.get_user_points(111), 0)
        await self.recover(None)
        self.assertEqual(await self.stored_status(), "unpublished")

    async def test_confirmed_post_awards_once_even_after_repeat_recovery(self):
        await self.add_award()
        channel = self.channel([self.review_message()])
        await self.recover(channel)
        self.assertEqual(await self.stored_status(), "awarded")
        self.assertEqual(await points.get_user_points(111), 50)
        await self.recover(channel)
        self.assertEqual(await points.get_user_points(111), 50)

    async def test_failure_mid_recovery_is_retryable_without_duplicate_credit(self):
        await self.add_award()
        channel = self.channel([self.review_message()])
        failure = AsyncMock(side_effect=RuntimeError("private customer payload"))
        await self.recover(channel, credit=failure)
        self.assertEqual(await self.stored_status(), "pending")
        self.assertEqual(await points.get_user_points(111), 0)
        status = await ops_health.health_snapshot(path=self.path)
        self.assertEqual(status["errors_24h"], 1)
        await self.recover(channel)
        self.assertEqual(await self.stored_status(), "awarded")
        self.assertEqual(await points.get_user_points(111), 50)

    async def test_previously_confirmed_pending_recovers_without_channel(self):
        await self.add_award(status="pending")
        await self.recover(None)
        self.assertEqual(await self.stored_status(), "awarded")

    async def test_duplicate_and_foreign_posts_do_not_change_ticket_matching(self):
        valid = self.review_message()
        fake = self.review_message(author=123)
        other = self.review_message(ticket=800)
        self.assertEqual(
            review_recovery.matching_review_posts([fake, other, valid], 999, {700}),
            {700: 555},
        )

    async def test_operational_log_does_not_persist_exception_messages(self):
        await ops_health.record_failure(
            "ticket_close", ValueError("private password secret 1234567890123456"),
            path=self.path,
        )
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT component,error_type FROM ops_health_events ORDER BY id DESC LIMIT 1"
            ) as cursor:
                self.assertEqual(
                    await cursor.fetchone(), ("ticket_close", "ValueError")
                )
        status = await ops_health.health_snapshot(path=self.path)
        self.assertEqual(status["errors_24h"], 1)


if __name__ == "__main__":
    unittest.main()
