"""Regression tests for one-time bot-authored DDS release announcements."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from database.services import update_announcement as notice


class FakeChannel:
    def __init__(self, guild_id=notice.GUILD_ID, user_id=42):
        self.guild = SimpleNamespace(id=guild_id)
        self.user_id = user_id
        self.messages = []
        self.send_calls = 0
        self.error_on_history = None

    async def history(self, limit=250):
        if self.error_on_history:
            raise self.error_on_history
        for message in self.messages[:limit]:
            yield message

    async def send(self, *, embed, allowed_mentions):
        self.send_calls += 1
        self.allowed_mentions = allowed_mentions
        message = SimpleNamespace(
            id=1000 + self.send_calls,
            author=SimpleNamespace(id=self.user_id),
            embeds=[embed],
        )
        self.messages.insert(0, message)
        return message


class OneTimeDDSAnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_path = str(Path(temp.name) / "update-test.db")
        database_patch = patch.object(notice, "DATABASE", db_path)
        database_patch.start()
        self.addCleanup(database_patch.stop)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.commit()
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=42),
            get_channel=lambda channel_id: self.channel,
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        channel_type_patch = patch.object(notice.discord, "TextChannel", FakeChannel)
        channel_type_patch.start()
        self.addCleanup(channel_type_patch.stop)

    async def test_announces_exactly_once_and_records_message_id(self):
        first = await notice.announce_once(self.bot)
        second = await notice.announce_once(self.bot)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.channel.send_calls, 1)
        async with aiosqlite.connect(notice.DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (notice.RELEASE_KEY,)
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], "1001")
        embed = self.channel.messages[0].embeds[0]
        self.assertEqual(embed.footer.text, notice.FOOTER)
        self.assertIn("Dialian", embed.title)
        descriptions = "\n".join(field.value for field in embed.fields)
        for item in ("30일", "5 Robux", "2+1", "3+1", "3분", "일괄"):
            self.assertIn(item, descriptions)

    async def test_existing_bot_post_is_recognized_after_crash_before_db_commit(self):
        self.channel.messages.append(
            SimpleNamespace(
                id=2030,
                author=SimpleNamespace(id=42),
                embeds=[notice.build_update_embed()],
            )
        )
        self.assertFalse(await notice.announce_once(self.bot))
        self.assertEqual(self.channel.send_calls, 0)
        async with aiosqlite.connect(notice.DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (notice.RELEASE_KEY,)
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], "2030")

    async def test_incorrect_server_never_receives_notice(self):
        self.channel.guild.id = 999
        with self.assertRaises(RuntimeError):
            await notice.announce_once(self.bot)
        self.assertEqual(self.channel.send_calls, 0)

    async def test_cannot_read_history_means_do_not_risk_a_duplicate(self):
        self.channel.error_on_history = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Missing read message history permission",
        )
        with self.assertRaises(discord.Forbidden):
            await notice.announce_once(self.bot)
        self.assertEqual(self.channel.send_calls, 0)

    async def test_recovery_after_post_succeeds_but_sqlite_write_failed(self):
        original_save = notice._save_posted
        with patch.object(notice, "_save_posted", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await notice.announce_once(self.bot)
        self.assertEqual(self.channel.send_calls, 1)
        # The same message exists in Discord's history and is recorded on retry.
        self.assertFalse(await notice.announce_once(self.bot))
        self.assertEqual(self.channel.send_calls, 1)


if __name__ == "__main__":
    unittest.main()
