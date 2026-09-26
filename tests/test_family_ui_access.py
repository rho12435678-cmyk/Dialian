"""Check UI preview form, exact ticket pricing and a safe repair for manually granted FAMILY roles."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

from config import BUYER_ROLE_ID, FAMILY_ROLE_ID, REGULAR_CUSTOMER_ROLE_ID
from database.services import family
from database.modal import ui_modal
from database.modal.gfx_modal import PurchaseModal


class UIPreviewFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_family_order_form_opens_after_package_selection(self):
        dropdown = ui_modal.UIQuantitySelect()
        dropdown._values = ["2+1 묶음"]
        interaction = SimpleNamespace(
            user=SimpleNamespace(roles=[]),
            response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock()),
        )
        with patch.object(ui_modal, "is_family_active", new=AsyncMock(return_value=True)):
            await dropdown.callback(interaction)
        interaction.response.send_modal.assert_awaited_once()
        self.assertIsInstance(interaction.response.send_modal.await_args.args[0], ui_modal.UIPreviewModal)
        self.assertEqual(interaction.response.send_modal.await_args.args[0].bundle_type, "2+1 묶음")

    async def test_non_member_cannot_open_ui_form(self):
        dropdown = ui_modal.UIQuantitySelect()
        dropdown._values = ["단품 (1개)"]
        interaction = SimpleNamespace(
            user=SimpleNamespace(roles=[]),
            response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock()),
        )
        with patch.object(ui_modal, "is_family_active", new=AsyncMock(return_value=False)):
            await dropdown.callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_submission_quotes_exact_ticket_instead_of_newest_database_row(self):
        channel=SimpleNamespace(id=4567,send=AsyncMock())
        member=SimpleNamespace(roles=[SimpleNamespace(id=REGULAR_CUSTOMER_ROLE_ID)])
        interaction=SimpleNamespace(user=member,followup=SimpleNamespace(send=AsyncMock()))
        modal=ui_modal.UIPreviewModal("2+1 묶음")
        with patch.object(ui_modal,"is_family_active",new=AsyncMock(return_value=True)), \
             patch.object(PurchaseModal,"create_ticket",new=AsyncMock(return_value=channel)) as base:
            await modal.create_ticket(interaction)
        base.assert_awaited_once_with(interaction)
        channel.send.assert_awaited_once()
        self.assertIn("7,000원",channel.send.await_args.args[0])


class FamilyTrialRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=str(Path(tmp.name)/"family.db")
        p=patch.object(family,"DATABASE",self.path)
        p.start()
        self.addCleanup(p.stop)
        await family.init_family_tables()
        self.guild=SimpleNamespace(id=250)
        self.member=SimpleNamespace(id=999,roles=[
            SimpleNamespace(id=BUYER_ROLE_ID), SimpleNamespace(id=FAMILY_ROLE_ID)
        ])
        self.started=family.utcnow()-timedelta(hours=2)
        self.ends=self.started+timedelta(days=7)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO family_trial_rollout VALUES (?,?,?)",
                (self.guild.id,self.started.isoformat(),self.ends.isoformat())
            )
            await db.commit()

    async def test_role_and_buyer_admin_repair_reuses_original_expiry(self):
        first=await family.reconcile_admin_confirmed_trial(self.guild,self.member)
        second=await family.reconcile_admin_confirmed_trial(self.guild,self.member)
        self.assertEqual(first,(True,self.ends.isoformat()))
        self.assertEqual(second,(False,self.ends.isoformat()))
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT kind,starts_at,expires_at FROM family_memberships WHERE guild_id=? AND user_id=?",
                (self.guild.id,self.member.id),
            ) as cursor:
                record=await cursor.fetchone()
        self.assertEqual(record,("trial",self.started.isoformat(),self.ends.isoformat()))

    async def test_role_without_buyer_is_not_enough(self):
        self.member.roles=[SimpleNamespace(id=FAMILY_ROLE_ID)]
        with self.assertRaises(ValueError):
            await family.reconcile_admin_confirmed_trial(self.guild,self.member)

    async def test_expired_rollout_cannot_be_extended(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE family_trial_rollout SET expires_at=? WHERE guild_id=?",
                ((family.utcnow()-timedelta(minutes=1)).isoformat(),self.guild.id),
            )
            await db.commit()
        with self.assertRaisesRegex(ValueError,"종료"):
            await family.reconcile_admin_confirmed_trial(self.guild,self.member)


if __name__=="__main__":
    unittest.main()
