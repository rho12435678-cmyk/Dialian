"""Ensure the stability update is short and sent only once per release."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from config import CUSTOMER_ROLE_ID
from database.services import stability_release_announcement as notice


class FakeChannel:
    def __init__(self, guild_id=notice.GUILD_ID):
        self.guild = SimpleNamespace(id=guild_id)
        self.messages = []
        self.sent = 0
        self.history_error = None

    async def history(self, limit):
        if self.history_error:
            raise self.history_error
        for message in self.messages[:limit]:
            yield message

    async def send(self, *, content, embed, allowed_mentions):
        self.sent += 1
        self.content = content
        self.allowed_mentions = allowed_mentions
        message = SimpleNamespace(
            id=1000+self.sent, author=SimpleNamespace(id=99), embeds=[embed],
        )
        self.messages.insert(0, message)
        return message


class StabilityNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "test.db")
        p = patch.object(notice, "DATABASE", self.path)
        p.start()
        self.addCleanup(p.stop)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("CREATE TABLE bot_settings(key TEXT PRIMARY KEY,value TEXT)")
            await db.commit()
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=99),
            get_channel=lambda cid: self.channel,
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        p = patch.object(notice.discord, "TextChannel", FakeChannel)
        p.start()
        self.addCleanup(p.stop)

    async def test_concise_customer_message_has_no_technical_jargon(self):
        embed = notice.build_stability_embed()
        self.assertEqual(len(embed.fields), 4)
        self.assertTrue(all(len(f.value) < 200 for f in embed.fields))
        body = " ".join(f.value for f in embed.fields)
        self.assertIn("결제 정보", body)
        self.assertIn("후기", body)
        self.assertIn("백업", body)
        self.assertNotIn("SQLite", body)
        self.assertNotIn("트랜잭션", body)
        self.assertEqual(embed.footer.text, notice.FOOTER)

    async def test_posts_once_on_updated_bot_and_pings_customer_role_only(self):
        self.assertTrue(await notice.announce_stability_once(self.bot))
        self.assertFalse(await notice.announce_stability_once(self.bot))
        self.assertEqual(self.channel.sent, 1)
        self.assertEqual(self.channel.content, f"<@&{CUSTOMER_ROLE_ID}>")
        self.assertEqual(
            [r.id for r in self.channel.allowed_mentions.roles], [CUSTOMER_ROLE_ID]
        )
        self.assertIs(self.channel.allowed_mentions.users, False)
        self.assertIs(self.channel.allowed_mentions.everyone, False)

    async def test_send_then_db_failure_recovers_from_discord_history(self):
        with patch.object(notice, "_record_post", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await notice.announce_stability_once(self.bot)
        self.assertFalse(await notice.announce_stability_once(self.bot))
        self.assertEqual(self.channel.sent, 1)

    async def test_no_history_access_or_wrong_server_means_no_ping(self):
        self.channel.history_error = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "history blocked"
        )
        with self.assertRaises(discord.Forbidden):
            await notice.announce_stability_once(self.bot)
        self.assertEqual(self.channel.sent, 0)
        self.channel.history_error = None
        self.channel.guild.id = 111
        with self.assertRaises(RuntimeError):
            await notice.announce_stability_once(self.bot)
        self.assertEqual(self.channel.sent, 0)

    async def test_parallel_calls_are_deduplicated(self):
        import asyncio
        result = await asyncio.gather(
            notice.announce_stability_once(self.bot),
            notice.announce_stability_once(self.bot),
        )
        self.assertEqual(result.count(True), 1)
        self.assertEqual(self.channel.sent, 1)

    async def test_startup_and_admin_retry_wiring(self):
        source = (Path(__file__).resolve().parents[1] / "dial.py").read_text("utf8")
        self.assertIn("publish_stability_release.start()", source)
        self.assertIn('name="안정화공지1회"', source)
        self.assertIn("await bot.wait_until_ready()", source)


if __name__ == "__main__":
    unittest.main()
