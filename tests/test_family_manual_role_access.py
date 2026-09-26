"""Regression tests for FAMILY role-based preview access within the original 7-day trial."""
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


class FamilyRoleAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = str(Path(tmp.name) / "family.db")
        p = patch.object(family, "DATABASE", self.db_path)
        p.start()
        self.addCleanup(p.stop)
        await family.init_family_tables()
        self.guild = SimpleNamespace(id=852)
        self.role = SimpleNamespace(id=FAMILY_ROLE_ID)
        self.member = SimpleNamespace(guild=self.guild, id=123, roles=[self.role])
        self.member_patch = patch.object(discord, "Member", SimpleNamespace)
        self.member_patch.start()
        self.addCleanup(self.member_patch.stop)
        self.started = family.utcnow() - timedelta(minutes=15)
        self.expires = self.started + timedelta(days=7)

    async def put_rollout(self, expires=None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO family_trial_rollout VALUES(?,?,?)",
                (self.guild.id, self.started.isoformat(),
                 (expires or self.expires).isoformat()),
            )
            await db.commit()

    async def read_record(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT kind,state,starts_at,expires_at FROM family_memberships WHERE guild_id=? AND user_id=?",
                (self.guild.id, self.member.id),
            ) as cur:
                return await cur.fetchone()

    async def test_manually_granted_role_works_without_existing_record(self):
        await self.put_rollout()
        self.assertTrue(await family.is_family_active(self.member))
        self.assertEqual(
            await self.read_record(),
            ("trial","active",self.started.isoformat(),self.expires.isoformat()),
        )
        # Repeated requests neither grant extra days nor insert duplicates.
        self.assertTrue(await family.is_family_active(self.member))
        self.assertEqual((await self.read_record())[3], self.expires.isoformat())

    async def test_role_only_without_live_rollout_does_not_grant_perpetual_access(self):
        self.assertFalse(await family.is_family_active(self.member))
        self.assertIsNone(await self.read_record())

    async def test_expired_rollout_cannot_reenroll_role_holder(self):
        await self.put_rollout(expires=family.utcnow()-timedelta(seconds=1))
        self.assertFalse(await family.is_family_active(self.member))
        self.assertIsNone(await self.read_record())

    async def test_revoked_membership_does_not_get_restored_from_role(self):
        await self.put_rollout()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO family_memberships VALUES(?,?,'trial',?,?,'expired')",
                (self.guild.id,self.member.id,self.started.isoformat(),self.expires.isoformat()),
            )
            await db.commit()
        self.assertFalse(await family.is_family_active(self.member))
        self.assertEqual((await self.read_record())[1],"expired")

    async def test_missing_role_cannot_get_in_with_persisted_record(self):
        await self.put_rollout()
        self.assertTrue(await family.is_family_active(self.member))
        self.member.roles = []
        self.assertFalse(await family.is_family_active(self.member))

    async def test_reconcile_registers_staff_granted_roles_with_original_expiry(self):
        await self.put_rollout()
        self.member.bot=False
        async def add_role(role,reason=None):
            if role not in self.member.roles:
                self.member.roles.append(role)
        async def remove_role(role,reason=None):
            if role in self.member.roles:
                self.member.roles.remove(role)
        self.member.add_roles=add_role
        self.member.remove_roles=remove_role
        self.role.members=[self.member]
        self.guild.get_role=lambda rid: self.role if rid==FAMILY_ROLE_ID else None
        self.guild.get_member=lambda uid: self.member if uid==self.member.id else None
        await family.reconcile_roles(self.guild)
        self.assertEqual((await self.read_record())[3], self.expires.isoformat())
        # End the original trial and verify automatic role removal.
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE family_trial_rollout SET expires_at=? WHERE guild_id=?",
                ((family.utcnow()-timedelta(seconds=1)).isoformat(),self.guild.id),
            )
            await db.execute(
                "UPDATE family_memberships SET expires_at=? WHERE guild_id=?",
                ((family.utcnow()-timedelta(seconds=1)).isoformat(),self.guild.id),
            )
            await db.commit()
        await family.reconcile_roles(self.guild)
        self.assertEqual(self.member.roles,[])
        self.assertFalse(await family.is_family_active(self.member))


if __name__=="__main__":
    unittest.main()
