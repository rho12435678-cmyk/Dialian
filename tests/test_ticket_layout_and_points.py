"""Regression tests for DDS ticket naming, bundle forms and point payouts."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from database import database
from database.modal.gfx_modal import PurchaseModal
from database.modal.uniform_modal import UniformModal
from database.services import point_ranking, points, ticket_layout


class TicketLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_designer_tier_and_member_nickname(self):
        customer = SimpleNamespace(display_name="고객 One")
        designer = SimpleNamespace(
            display_name="ParkSunny",
            roles=[SimpleNamespace(name="GFX 상급 디자이너")],
        )
        self.assertEqual(
            ticket_layout.ticket_name("GFX", customer, designer, suffix="abc123"),
            "티켓-gfx-상급-parksunny-고객one-abc123",
        )
        self.assertEqual(
            ticket_layout.ticket_name("Roblox 복장", customer, designer, suffix="abc123"),
            "티켓-복장-parksunny-고객one-abc123",
        )

    async def test_unassigned_application_and_partner_names(self):
        customer = SimpleNamespace(display_name="Applicant")
        for category, label in (("개발자 지원", "지원"), ("파트너 문의", "파트너")):
            name = ticket_layout.ticket_name(category, customer, suffix="123456")
            self.assertEqual(name, f"티켓-{label}-미배정-applicant-123456")

    async def test_creates_private_category_at_top(self):
        created = SimpleNamespace(
            channels=[], edit=AsyncMock(), id=999, name="🎨 DDS｜GFX 진행 티켓"
        )
        guild = SimpleNamespace(
            categories=[],
            default_role=discord.Object(id=1),
            create_category=AsyncMock(return_value=created),
        )
        category = await ticket_layout.get_or_create_ticket_category(guild, "GFX")
        self.assertIs(category, created)
        guild.create_category.assert_awaited_once()
        args, kwargs = guild.create_category.await_args
        self.assertEqual(args[0], created.name)
        self.assertFalse(kwargs["overwrites"][guild.default_role].view_channel)
        created.edit.assert_awaited_once()

    async def test_relocating_existing_ticket_does_not_sync_permissions(self):
        customer = SimpleNamespace(display_name="Buyer")
        designer = SimpleNamespace(
            display_name="Designer", roles=[SimpleNamespace(name="중급 GFX")]
        )
        target = SimpleNamespace(id=888)
        guild = SimpleNamespace()
        channel = SimpleNamespace(
            id=123456789,
            guild=guild,
            category_id=111,
            name="티켓-gfx-old",
            edit=AsyncMock(),
        )
        with patch.object(ticket_layout, "get_or_create_ticket_category",
                          new=AsyncMock(return_value=target)):
            await ticket_layout.organize_existing_ticket(
                channel, "GFX", customer, designer
            )
        self.assertEqual(channel.edit.await_args.kwargs["category"], target)
        self.assertFalse(channel.edit.await_args.kwargs["sync_permissions"])
        self.assertIn("중급-designer-buyer", channel.edit.await_args.kwargs["name"])


class BundleNicknameTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_active_forms_require_roblox_nickname(self):
        for modal_type in (PurchaseModal, UniformModal):
            for bundle in ("단품 (1개)", "2+1 묶음", "3+1 묶음"):
                with self.subTest(modal=modal_type.__name__, bundle=bundle):
                    form = modal_type(bundle_type=bundle)
                    nickname_inputs = [
                        item for item in form.children
                        if isinstance(item, discord.ui.TextInput)
                        and "Roblox 닉네임" in item.label
                    ]
                    self.assertEqual(len(nickname_inputs), 1)
                    self.assertTrue(nickname_inputs[0].required)
                    self.assertLessEqual(len(form.children), 5)
                    if bundle != "단품 (1개)":
                        self.assertIsNotNone(form.fourth_style)


class PointCreditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / "dialian.db")
        for module in (database, point_ranking, points):
            p = patch.object(module, "DATABASE", self.db_path)
            p.start()
            self.addCleanup(p.stop)
        await database.create_tables()
        self.member = discord.Object(id=123)

    async def insert_review(self, ticket, award_status=None, amount=0):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO reviews
                   (ticket_channel, customer_id, developer_id, stars, review, created_at)
                   VALUES (?, 123, 456, 5, '', '2026-09-20T08:00:00')""",
                (ticket,),
            )
            if award_status:
                await db.execute(
                    """INSERT INTO review_point_awards
                       (ticket_channel, customer_id, amount, status)
                       VALUES (?, 123, ?, ?)""",
                    (ticket, amount, award_status),
                )
            await db.commit()

    async def test_new_review_awards_only_once(self):
        await self.insert_review(777, "pending", points.REVIEW_POINTS_2_PLUS_1)
        first = await points.credit_review_award(None, self.member, 777)
        second = await points.credit_review_award(None, self.member, 777)
        self.assertEqual(first, (points.REVIEW_POINTS_2_PLUS_1,
                                 points.REVIEW_POINTS_2_PLUS_1, True))
        self.assertEqual(second, (0, points.REVIEW_POINTS_2_PLUS_1, False))
        self.assertEqual(await points.get_user_points(123), points.REVIEW_POINTS_2_PLUS_1)

    async def test_preexisting_reviews_need_explicit_administrator_approval(self):
        await self.insert_review(888)
        await database.create_tables()
        with self.assertRaises(ValueError):
            await points.credit_review_award(None, self.member, 888)
        amount, total, credited = await points.credit_review_award(
            None, self.member, 888,
            administrator_id=555,
            legacy_amount=points.REVIEW_POINTS_SINGLE,
        )
        self.assertEqual((amount, total, credited),
                         (points.REVIEW_POINTS_SINGLE, points.REVIEW_POINTS_SINGLE, True))
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, approved_by FROM review_point_awards WHERE ticket_channel=888"
            ) as cursor:
                self.assertEqual(await cursor.fetchone(), ("awarded", 555))
        second = await points.credit_review_award(
            None, self.member, 888,
            administrator_id=555,
            legacy_amount=points.REVIEW_POINTS_SINGLE,
        )
        self.assertEqual(second, (0, points.REVIEW_POINTS_SINGLE, False))

    async def test_wrong_customer_does_not_receive_review_credit(self):
        await self.insert_review(999, "pending", points.REVIEW_POINTS_SINGLE)
        with self.assertRaises(ValueError):
            await points.credit_review_award(None, discord.Object(id=111), 999)
        self.assertEqual(await points.get_user_points(123), 0)

    async def test_migration_never_automatically_credits_old_reviews(self):
        await self.insert_review(1001)
        await database.create_tables()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, amount FROM review_point_awards WHERE ticket_channel=1001"
            ) as cursor:
                self.assertEqual(await cursor.fetchone(), ("legacy_unverified", 0))
        self.assertEqual(await points.get_user_points(123), 0)


if __name__ == "__main__":
    unittest.main()
