"""DDS FAMILY price, entitlement and preview regression tests."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiosqlite
import discord

from config import FAMILY_ROLE_ID
from database.services import family
from database.modal.ui_modal import UI_BASE, UIPreviewModal
from database.services.ticket_layout import ticket_kind, ticket_label


class FamilyPricingTests(unittest.TestCase):
    def test_additive_discount_never_stacks_to_forty_percent(self):
        self.assertEqual(family.discount_rate(False, False), 0)
        self.assertEqual(family.discount_rate(False, True), 20)
        self.assertEqual(family.discount_rate(True, False), 20)
        self.assertEqual(family.discount_rate(True, True), 30)

    def test_ui_price_matrix(self):
        self.assertEqual(
            [UI_BASE[k] for k in ("단품 (1개)", "2+1 묶음", "3+1 묶음")],
            [5000, 10000, 15000],
        )
        self.assertEqual(
            [family.discounted_price(v, True, False) for v in UI_BASE.values()],
            [4000, 8000, 12000],
        )
        self.assertEqual(
            [family.discounted_price(v, True, True) for v in UI_BASE.values()],
            [3500, 7000, 10500],
        )

    def test_ui_modal_uses_existing_ticket_workflow(self):
        for package in UI_BASE:
            with self.subTest(package=package):
                modal = UIPreviewModal(package)
                self.assertEqual(modal.COMMISSION_NAME, "Roblox UI 사전 체험")
                self.assertLessEqual(len(modal.children), 5)
                self.assertTrue(all(item.required for item in modal.children))

    def test_ui_ticket_is_separate_private_category(self):
        self.assertEqual(ticket_kind("Roblox UI 사전 체험"), "ui")
        self.assertEqual(ticket_label("Roblox UI 사전 체험"), "ui")


class FamilyEntitlementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = str(Path(temp.name) / "family.db")
        p = patch.object(family, "DATABASE", path)
        p.start()
        self.addCleanup(p.stop)
        await family.init_family_tables()
        self.path = path

    async def test_active_requires_both_db_expiry_and_discord_role(self):
        class FakeMember:
            def __init__(self, roles):
                self.guild = SimpleNamespace(id=7)
                self.id = 23
                self.roles = roles
        roles = [SimpleNamespace(id=FAMILY_ROLE_ID)]
        expiry = (family.utcnow() + timedelta(days=7)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO family_memberships VALUES(?,?,'trial',?,?,'active')",
                (7, 23, family.utcnow().isoformat(), expiry),
            )
            await db.commit()
        with patch.object(discord, "Member", FakeMember):
            self.assertTrue(await family.is_family_active(FakeMember(roles)))
            self.assertFalse(await family.is_family_active(FakeMember([])))
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "UPDATE family_memberships SET expires_at=? WHERE guild_id=7 AND user_id=23",
                    ((family.utcnow() - timedelta(seconds=1)).isoformat(),),
                )
                await db.commit()
            self.assertFalse(await family.is_family_active(FakeMember(roles)))


if __name__ == "__main__":
    unittest.main()
