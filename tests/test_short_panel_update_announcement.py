"""Verify that Dialian sends the concise 3-category release notice only once."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

from config import CUSTOMER_ROLE_ID, PURCHASE_CHANNEL_ID
from database.services import short_panel_update_announcement as notice


class FakeUpdateChannel:
    def __init__(self, guild_id=notice.GUILD_ID, author_id=99):
        self.guild = SimpleNamespace(id=guild_id)
        self.author_id = author_id
        self.messages = []
        self.sent = 0
        self.history_error = None

    async def history(self, limit):
        if self.history_error is not None:
            raise self.history_error
        for message in self.messages[:limit]:
            yield message

    async def send(self, *, content, embed, allowed_mentions):
        self.sent += 1
        self.content = content
        self.allowed_mentions = allowed_mentions
        message = SimpleNamespace(
            id=7000+self.sent,
            author=SimpleNamespace(id=self.author_id),
            embeds=[embed],
        )
        self.messages.insert(0, message)
        return message


class ShortPanelAnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = str(Path(temp.name) / "short-panel.db")
        db_patch = patch.object(notice, "DATABASE", self.db_path)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT)")
            await db.commit()
        self.channel = FakeUpdateChannel()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=99),
            get_channel=lambda cid: self.channel,
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        channel_patch = patch.object(notice.discord, "TextChannel", FakeUpdateChannel)
        channel_patch.start()
        self.addCleanup(channel_patch.stop)

    async def test_short_categories_without_old_launch_information(self):
        embed = notice.build_short_panel_update_embed()
        self.assertEqual(len(embed.fields), 4)
        self.assertEqual([f.name[:2] for f in embed.fields[:3]],
                         ["🎨", "🤝", "📋"])
        body = " ".join(f.value for f in embed.fields)
        for item in ("GFX", "Roblox 복장", "UI 사전 체험", "개발자 지원",
                     "파트너 문의", "일반", "단골 20%", "FAMILY 20%",
                     "FAMILY+단골 30%", "바리에이션", str(PURCHASE_CHANNEL_ID)):
            self.assertIn(item, body)
        self.assertNotIn("7일", body)
        self.assertNotIn("15P", body)
        self.assertNotIn("자동 결제", body)
        self.assertIn("다시 게시", body)
        self.assertEqual(embed.footer.text, notice.FOOTER)

    async def test_startup_posts_as_dialian_and_pings_customer_role_once(self):
        self.assertTrue(await notice.announce_short_panel_update_once(self.bot))
        self.assertFalse(await notice.announce_short_panel_update_once(self.bot))
        self.assertEqual(self.channel.sent, 1)
        self.assertEqual(self.channel.content, f"<@&{CUSTOMER_ROLE_ID}>")
        self.assertEqual(
            [x.id for x in self.channel.allowed_mentions.roles], [CUSTOMER_ROLE_ID]
        )
        self.assertIs(self.channel.allowed_mentions.users, False)
        self.assertIs(self.channel.allowed_mentions.everyone, False)
        self.assertEqual(self.channel.messages[0].embeds[0].footer.text, notice.FOOTER)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (notice.RELEASE_KEY,)
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], "7001")

    async def test_crash_between_send_and_db_write_recovers_without_new_ping(self):
        with patch.object(notice, "_record_post", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await notice.announce_short_panel_update_once(self.bot)
        self.assertFalse(await notice.announce_short_panel_update_once(self.bot))
        self.assertEqual(self.channel.sent, 1)

    async def test_history_permission_failure_does_not_risk_duplicate_ping(self):
        self.channel.history_error = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "no history"
        )
        with self.assertRaises(discord.Forbidden):
            await notice.announce_short_panel_update_once(self.bot)
        self.assertEqual(self.channel.sent, 0)

    async def test_wrong_server_and_bot_not_ready_do_not_send(self):
        self.channel.guild.id = 777
        with self.assertRaises(RuntimeError):
            await notice.announce_short_panel_update_once(self.bot)
        self.bot.user = None
        with self.assertRaises(RuntimeError):
            await notice.announce_short_panel_update_once(self.bot)
        self.assertEqual(self.channel.sent, 0)

    async def test_dialian_wiring_starts_once_and_has_admin_retry(self):
        code = (Path(__file__).resolve().parents[1] / "dial.py").read_text("utf8")
        self.assertIn("publish_short_panel_update.start()", code)
        self.assertIn('name="간단업뎃공지1회"', code)
        self.assertIn("await bot.wait_until_ready()", code)


if __name__ == "__main__":
    unittest.main()
