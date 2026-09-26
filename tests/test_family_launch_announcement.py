"""Regression tests for DDS FAMILY actual-start one-time announcement."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from database.services import family
from database.services import family_launch_announcement as notice


class FakeChannel:
    def __init__(self, guild_id=notice.GUILD_ID, author_id=99):
        self.guild = SimpleNamespace(id=guild_id)
        self.author_id = author_id
        self.messages = []
        self.send_calls = 0
        self.history_failure = None

    async def history(self, limit=500):
        if self.history_failure:
            raise self.history_failure
        for message in self.messages[:limit]:
            yield message

    async def send(self, *, content, embed, allowed_mentions):
        self.send_calls += 1
        self.content = content
        self.allowed_mentions = allowed_mentions
        message = SimpleNamespace(
            id=5000+self.send_calls,
            author=SimpleNamespace(id=self.author_id),
            embeds=[embed],
        )
        self.messages.insert(0, message)
        return message


class FamilyLaunchAnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_path = str(Path(temp.name) / "family-launch.db")
        for target in (notice, family):
            p = patch.object(target, "DATABASE", db_path)
            p.start()
            self.addCleanup(p.stop)
        await notice.init_family_tables()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT)")
            started = family.utcnow() - timedelta(minutes=2)
            expires = started + timedelta(days=7)
            await db.execute(
                "INSERT INTO family_trial_rollout VALUES (?, ?, ?)",
                (notice.GUILD_ID, started.isoformat(), expires.isoformat()),
            )
            await db.execute(
                "INSERT INTO family_memberships VALUES (?, ?, 'trial', ?, ?, 'active')",
                (notice.GUILD_ID, 123, started.isoformat(), expires.isoformat()),
            )
            await db.commit()
        self.db_path = db_path
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=99),
            get_guild=lambda guild_id: SimpleNamespace(id=guild_id),
            get_channel=lambda channel_id: self.channel,
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        channel_patch = patch.object(notice.discord, "TextChannel", FakeChannel)
        channel_patch.start()
        self.addCleanup(channel_patch.stop)
        rollout_patch = patch.object(notice, "start_trial_once", new=AsyncMock())
        rollout_patch.start()
        self.addCleanup(rollout_patch.stop)

    async def test_announces_exactly_once_with_verified_live_dates(self):
        self.assertTrue(await notice.announce_family_launch_once(self.bot))
        self.assertFalse(await notice.announce_family_launch_once(self.bot))
        self.assertEqual(self.channel.send_calls, 1)
        self.assertEqual(self.channel.content, f"<@&{notice.CUSTOMER_ROLE_ID}>")
        self.assertEqual(
            [role.id for role in self.channel.allowed_mentions.roles],
            [notice.CUSTOMER_ROLE_ID],
        )
        self.assertIs(self.channel.allowed_mentions.users, False)
        self.assertIs(self.channel.allowed_mentions.everyone, False)
        embed = self.channel.messages[0].embeds[0]
        self.assertEqual(embed.footer.text, notice.FOOTER)
        self.assertIn("오늘부터", embed.description)
        body = "\n".join(field.value for field in embed.fields)
        for fragment in ("7일", "15P", "30%", "20%", "75P", "150P", "225P",
                         "12,000원", "10,500원", "자동 결제", "모든 일반 회원",
                         "패널 교체 후"):
            self.assertIn(fragment, body)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (notice.RELEASE_KEY,)
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], "5001")

    async def test_discord_send_before_db_save_recovers_without_second_ping(self):
        with patch.object(notice, "_save_post", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await notice.announce_family_launch_once(self.bot)
        self.assertEqual(self.channel.send_calls, 1)
        self.assertFalse(await notice.announce_family_launch_once(self.bot))
        self.assertEqual(self.channel.send_calls, 1)

    async def test_no_read_history_means_no_post(self):
        self.channel.history_failure = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "no history")
        with self.assertRaises(discord.Forbidden):
            await notice.announce_family_launch_once(self.bot)
        self.assertEqual(self.channel.send_calls, 0)

    async def test_no_trial_record_means_no_false_launch_notice(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM family_trial_rollout")
            await db.commit()
        with self.assertRaisesRegex(RuntimeError, "체험 대상"):
            await notice.announce_family_launch_once(self.bot)
        self.assertEqual(self.channel.send_calls, 0)

    async def test_expired_trial_does_not_trigger_late_announcement(self):
        async with aiosqlite.connect(self.db_path) as db:
            old = family.utcnow() - timedelta(days=8)
            await db.execute(
                "UPDATE family_trial_rollout SET started_at=?, expires_at=? WHERE guild_id=?",
                (old.isoformat(), (old+timedelta(days=7)).isoformat(), notice.GUILD_ID),
            )
            await db.commit()
        self.assertFalse(await notice.announce_family_launch_once(self.bot))
        self.assertEqual(self.channel.send_calls, 0)

    async def test_incorrect_server_never_gets_announcement(self):
        self.channel.guild.id=777
        with self.assertRaises(RuntimeError):
            await notice.announce_family_launch_once(self.bot)
        self.assertEqual(self.channel.send_calls, 0)


if __name__ == "__main__":
    unittest.main()
