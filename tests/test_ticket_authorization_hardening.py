"""Regression tests for database-first ticket permissions and spoofed legacy posts."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiosqlite

from config import DESIGNER_ROLE_IDS
from database import database
from database.services import ticket_access


def member(user_id, *, admin=False, role_id=None):
    return SimpleNamespace(
        id=user_id,
        guild_permissions=SimpleNamespace(administrator=admin),
        roles=([SimpleNamespace(id=role_id)] if role_id else []),
    )


class FakeChannel:
    def __init__(self, channel_id, messages=None, bot_id=999):
        self.id = channel_id
        self.guild = SimpleNamespace(me=SimpleNamespace(id=bot_id))
        self.messages = messages or []

    async def history(self, **kwargs):
        for message in self.messages:
            yield message


def application_message(author_id, designer_id, title="📋 GFX 커미션 신청서"):
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id),
        embeds=[SimpleNamespace(
            title=title,
            fields=[SimpleNamespace(
                name="👨‍💻 담당 디자이너", value=f"<@{designer_id}>"
            )],
        )],
    )


class TicketPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "tickets.db")
        patcher = patch.object(database, "DATABASE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        await database.create_tables()

    async def add_ticket(self, ticket_id, designer_id, category="GFX",
                         status="in_progress"):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO commissions(
                     ticket_channel, customer_id, designer_id, category, status)
                   VALUES (?,?,?,?,?)""",
                (ticket_id, 111, designer_id, category, status),
            )
            await db.commit()

    async def test_db_reassignment_overrides_spoofed_and_stale_messages(self):
        await self.add_ticket(100, 222)
        # A forged mention and even an old bot application must never override DB.
        channel = FakeChannel(100, [
            application_message(123, 123), application_message(999, 333),
        ])
        assignment = await ticket_access.ticket_assignment(channel)
        self.assertTrue(assignment.known)
        self.assertFalse(assignment.legacy)
        self.assertEqual(assignment.designer_id, 222)
        self.assertTrue(ticket_access.can_manage_assignment(member(222), assignment))
        self.assertFalse(ticket_access.can_manage_assignment(member(333), assignment))
        self.assertTrue(ticket_access.can_manage_assignment(member(444, admin=True), assignment))

    async def test_unclaimed_only_matching_specialty_and_no_closed_ticket(self):
        await self.add_ticket(101, 0, category="GFX")
        await self.add_ticket(102, 0, category="Roblox 복장")
        await self.add_ticket(103, 0, category="파트너 문의")
        await self.add_ticket(104, 222, status="closed")
        gfx = member(225, role_id=DESIGNER_ROLE_IDS["gfx"])
        uniform = member(226, role_id=DESIGNER_ROLE_IDS["uniform"])
        self.assertTrue(await ticket_access.can_manage_channel(gfx, FakeChannel(101)))
        self.assertFalse(await ticket_access.can_manage_channel(uniform, FakeChannel(101)))
        self.assertTrue(await ticket_access.can_manage_channel(uniform, FakeChannel(102)))
        self.assertFalse(await ticket_access.can_manage_channel(gfx, FakeChannel(103)))
        self.assertFalse(await ticket_access.can_manage_channel(member(222), FakeChannel(104)))

    async def test_legacy_fallback_checks_bot_and_exact_assignment_field(self):
        forged = application_message(123, 111)
        channel = FakeChannel(200, [forged])
        self.assertFalse((await ticket_access.ticket_assignment(channel)).known)
        legitimate = application_message(999, 222)
        channel.messages.append(legitimate)
        assignment = await ticket_access.ticket_assignment(channel)
        self.assertTrue(assignment.legacy)
        self.assertEqual(assignment.designer_id, 222)
        self.assertFalse(ticket_access.can_manage_assignment(member(111), assignment))
        self.assertTrue(ticket_access.can_manage_assignment(member(222), assignment))

    async def test_unknown_without_bot_provenance_is_fail_closed(self):
        unknown = await ticket_access.ticket_assignment(FakeChannel(300))
        self.assertFalse(ticket_access.can_manage_assignment(
            member(111, role_id=DESIGNER_ROLE_IDS["gfx"]), unknown
        ))


if __name__ == "__main__":
    unittest.main()
