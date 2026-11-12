import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import discord

from config import Settings
from ddh.backups import backup
from ddh.blueprint import CATEGORIES, CHANNELS, FIELDS, ROLES, overwrites
from ddh.bot import DDHCommands, Dialian
from ddh.content import publish_panels
from ddh.notices import due_date, publish_due
from ddh.server import UserError, preflight, provision, roles_for
from ddh.security import specifications
from ddh.store import Store
from ddh.tickets import validate_payload
from ddh.views import RulesView, SupportView, TicketActions
from fakes import Guild, Text


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guild = Guild()
        self.settings = Settings("test", self.guild.id, Path(self.tmp.name) / "ddh.db")
        self.bot = Dialian(self.settings)
        self.addAsyncCleanup(self.bot.close)
        await self.bot.store.initialize()
        await provision(self.guild, self.bot.store, self.settings)
        await self.bot.store.set(self.guild.id, "setup_complete", True)
        await self.bot.store.set(self.guild.id, "setup_at", "2026-11-12T00:00:00+00:00")
        self.roles = await roles_for(self.guild, self.bot.store)
        self.member = self.guild.member(3, [self.roles["member"]])
        self.staff = self.guild.member(4, [self.roles["staff"]])
        self.visitor = self.guild.member(5)

    async def channel(self, key):
        return self.guild.get_channel(await self.bot.store.get(self.guild.id, f"channel:{key}"))

    def payload(self):
        return {"_fields": ["gfx", "vfx"], "Roblox 사용자명·프로필": "Dial",
                "포트폴리오 링크": "https://example.com/works", "경력·도구·본인 담당 부분": "Blender, 본인 작업"}

    async def test_setup_repeatedly_repairs_without_duplicates(self):
        counts = len(self.guild.channels), len(self.guild.roles)
        channel = await self.channel("general")
        channel.name = "renamed"
        await provision(self.guild, self.bot.store, self.settings)
        self.assertEqual(counts, (len(self.guild.channels), len(self.guild.roles)))
        self.assertEqual(channel.name, "자유채팅")
        self.assertEqual(counts, (len(CATEGORIES) + len(CHANNELS), len(ROLES) + 2))
        self.assertFalse(self.guild.default_role.permissions.send_messages)
        self.assertTrue(all(not r.permissions.administrator for r in self.roles.values()))

    async def test_adopts_original_screenshot_channels(self):
        guild = Guild(self.guild.id + 1)
        original = await guild.create_text_channel("general")
        lounge = await guild.create_voice_channel("Lounge")
        await provision(guild, self.bot.store, Settings("test", guild.id))
        self.assertEqual(await self.bot.store.get(guild.id, "channel:general"), original.id)
        self.assertEqual(await self.bot.store.get(guild.id, "channel:lounge"), lounge.id)

    async def test_duplicate_names_fail_before_mutations(self):
        guild = Guild(self.guild.id + 1)
        await guild.create_text_channel("general")
        await guild.create_text_channel("자유채팅")
        with self.assertRaises(UserError):
            await preflight(guild, self.bot.store)
        self.assertEqual(len(guild.roles), 2)

    async def test_missing_permission_prevents_setup(self):
        self.guild.me.guild_permissions.manage_roles = False
        with self.assertRaises(UserError):
            await preflight(self.guild, self.bot.store)

    async def test_hierarchy_blocks_unmanageable_role(self):
        self.roles["field:gfx"].position = 250
        with self.assertRaises(UserError):
            await preflight(self.guild, self.bot.store)

    async def test_public_permission_matrix_uses_discord_resolution(self):
        general = await self.channel("general")
        rules = await self.channel("rules")
        portfolio = await self.channel("portfolio:gfx")
        voice = await self.channel("lounge")
        gfx = self.guild.member(6, [self.roles["member"], self.roles["developer"], self.roles["field:gfx"]])
        vfx = self.guild.member(7, [self.roles["member"], self.roles["developer"], self.roles["field:vfx"]])
        self.assertTrue(general.permissions_for(self.visitor).view_channel)
        self.assertFalse(general.permissions_for(self.visitor).send_messages)
        self.assertTrue(general.permissions_for(self.member).send_messages)
        self.assertFalse(rules.permissions_for(self.member).send_messages)
        self.assertFalse(portfolio.permissions_for(vfx).send_messages)
        self.assertTrue(portfolio.permissions_for(gfx).send_messages)
        self.assertFalse(voice.permissions_for(self.visitor).connect)
        self.assertTrue(voice.permissions_for(self.member).connect)
        self.assertFalse((await self.channel("staff_chat")).permissions_for(self.member).view_channel)

    async def test_private_ticket_only_owner_staff_and_bot(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        for person in (self.member, self.staff, self.guild.me):
            self.assertTrue(channel.permissions_for(person).view_channel)
        for person in (self.visitor, self.guild.member(8, [self.roles["developer"], self.roles["partner"]])):
            self.assertFalse(channel.permissions_for(person).view_channel)
        self.assertFalse(channel.permissions_for(self.member).mention_everyone)
        self.assertFalse(channel.permissions_for(self.member).create_public_threads)

    async def test_duplicate_submissions_return_one_ticket(self):
        channels = await asyncio.gather(*[self.bot.tickets.create(self.guild, self.member, "developer", self.payload()) for _ in range(3)])
        self.assertEqual(len({c.id for c in channels}), 1)
        self.assertEqual(len(await self.bot.store.tickets(self.guild.id, "open")), 1)

    async def test_db_constraint_survives_two_store_instances(self):
        second = Store(self.settings.database)
        await self.bot.store.reserve(self.guild.id, 99, "feedback", {"제목": "a", "내용": "b"})
        with self.assertRaises(sqlite3.IntegrityError):
            await second.reserve(self.guild.id, 99, "feedback", {})
        self.assertIsNone(await second.active(self.guild.id + 1, 99, "feedback"))

    async def test_wrong_guild_and_nonmember_denied(self):
        with self.assertRaises(UserError):
            await self.bot.tickets.create(Guild(self.guild.id + 1), self.member, "developer", self.payload())
        with self.assertRaises(UserError):
            await self.bot.tickets.create(self.guild, self.visitor, "developer", self.payload())

    async def test_approve_grants_only_selected_fields_and_is_single_use(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        with self.assertRaises(UserError):
            await self.bot.tickets.review(self.guild, channel, self.member, True, "self")
        await self.bot.tickets.review(self.guild, channel, self.staff, True, "본인 작품 확인")
        for key in ("developer", "field:gfx", "field:vfx"):
            self.assertIn(self.roles[key], self.member.roles)
        self.assertNotIn(self.roles["staff"], self.member.roles)
        self.assertNotIn(self.roles["field:ui"], self.member.roles)
        with self.assertRaises(UserError):
            await self.bot.tickets.review(self.guild, channel, self.staff, False, "second")

    async def test_partner_approval_and_feedback_rejection(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "partner", {
            "서버 이름": "Example", "초대 링크": "https://discord.gg/example", "서버 소개·제휴 제안": "개발 커뮤니티"})
        await self.bot.tickets.review(self.guild, channel, self.staff, True, "승인")
        self.assertIn(self.roles["partner"], self.member.roles)
        other = self.guild.member(21, [self.roles["member"]])
        feedback = await self.bot.tickets.create(self.guild, other, "feedback", {"제목": "건의", "내용": "테스트"})
        with self.assertRaises(UserError):
            await self.bot.tickets.review(self.guild, feedback, self.staff, True, "승인")

    async def test_rejection_does_not_grant_roles(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        await self.bot.tickets.review(self.guild, channel, self.staff, False, "자료 부족")
        self.assertNotIn(self.roles["developer"], self.member.roles)
        ticket = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(ticket["decision"], "rejected")

    async def test_api_role_failure_keeps_approval_recoverable(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        self.member.add_roles.side_effect = RuntimeError("network")
        with self.assertRaises(RuntimeError):
            await self.bot.tickets.review(self.guild, channel, self.staff, True, "승인")
        ticket = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(ticket["decision"], "approving")
        with self.assertRaises(UserError):
            await self.bot.tickets.review(self.guild, channel, self.staff, False, "cannot undo partial grant")

    async def test_approval_recovers_after_grant_before_decision_save(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        original = self.bot.store.update_ticket
        async def fail_approved(*args, **kwargs):
            if kwargs.get("decision") == "approved":
                raise RuntimeError("DB failure after roles granted")
            return await original(*args, **kwargs)
        with patch.object(self.bot.store, "update_ticket", side_effect=fail_approved):
            with self.assertRaises(RuntimeError):
                await self.bot.tickets.review(self.guild, channel, self.staff, True, "verified")
        self.assertIn(self.roles["developer"], self.member.roles)
        await self.bot.tickets.recover(self.guild)
        self.assertEqual((await self.bot.store.channel_ticket(self.guild.id, channel.id))["decision"], "approved")

    async def test_close_preserves_review_reason(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        await self.bot.tickets.review(self.guild, channel, self.staff, True, "작품 확인")
        await self.bot.tickets.close(self.guild, channel, self.member, "답변 완료")
        ticket = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(ticket["reason"], "작품 확인")
        self.assertEqual(ticket["close_reason"], "답변 완료")
        self.assertEqual(ticket["closed_by"], self.member.id)

    async def test_modified_developer_role_cannot_escalate_applicant(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        self.roles["field:gfx"]._permissions = discord.Permissions(manage_roles=True).value
        with self.assertRaises(UserError):
            await self.bot.tickets.review(self.guild, channel, self.staff, True, "승인")
        self.member.add_roles.assert_not_called()

    async def test_legacy_database_is_refused_without_modification(self):
        path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE commissions(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO commissions VALUES (42)")
        with self.assertRaisesRegex(RuntimeError, "DDS"):
            await Store(path).initialize()
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT id FROM commissions").fetchone()[0], 42)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='tickets'").fetchone())

    async def test_creation_recovers_after_discord_success_before_db_save(self):
        real_update = self.bot.store.update_ticket
        failed = False
        async def update(*args, **kwargs):
            nonlocal failed
            if "channel_id" in kwargs and not failed:
                failed = True
                raise RuntimeError("DB unavailable after channel creation")
            return await real_update(*args, **kwargs)
        with patch.object(self.bot.store, "update_ticket", side_effect=update):
            with self.assertRaises(RuntimeError):
                await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        before = len(self.guild.channels)
        await self.bot.tickets.recover(self.guild)
        self.assertEqual(before, len(self.guild.channels))
        self.assertEqual(len(await self.bot.store.tickets(self.guild.id, "open")), 1)

    async def test_close_failure_is_recovered_and_archive_is_private(self):
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", self.payload())
        with self.assertRaises(UserError):
            await self.bot.tickets.close(self.guild, channel, self.visitor, "no")
        channel.fail_edit = True
        with self.assertRaises(RuntimeError):
            await self.bot.tickets.close(self.guild, channel, self.member, "완료")
        self.assertEqual((await self.bot.store.channel_ticket(self.guild.id, channel.id))["state"], "closing")
        channel.fail_edit = False
        await self.bot.tickets.recover(self.guild)
        self.assertEqual((await self.bot.store.channel_ticket(self.guild.id, channel.id))["state"], "closed")
        self.assertFalse(channel.permissions_for(self.member).view_channel)
        self.assertTrue(channel.permissions_for(self.staff).view_channel)

    async def test_rules_button_cannot_grant_a_modified_powerful_role(self):
        role = self.roles["member"]
        role._permissions = discord.Permissions(manage_roles=True).value
        interaction = AsyncMock()
        interaction.guild = self.guild
        interaction.user = self.visitor
        with self.assertRaises(UserError):
            await RulesView(self.bot).accept.callback(interaction)
        self.visitor.add_roles.assert_not_called()

    async def test_panels_are_persistent_and_repair_without_duplicates(self):
        for view_type in (RulesView, SupportView, TicketActions):
            self.assertTrue(view_type(self.bot).is_persistent())
        await publish_panels(self.bot, self.guild)
        counts = [len(c.messages) for c in self.guild.channels if isinstance(c, Text)]
        await publish_panels(self.bot, self.guild)
        self.assertEqual(counts, [len(c.messages) for c in self.guild.channels if isinstance(c, Text)])
        support = await self.channel("support_panel")
        support.messages.clear()
        await publish_panels(self.bot, self.guild)
        self.assertEqual(len(support.messages), 1)

    async def test_scheduled_notice_restart_and_disabled(self):
        now = datetime(2026, 11, 12, 9, 1, tzinfo=timezone.utc)
        self.assertTrue(await publish_due(self.bot, self.guild, now))
        self.bot.store = Store(self.settings.database)
        self.assertFalse(await publish_due(self.bot, self.guild, now))
        self.assertEqual(len((await self.channel("announcements")).messages), 1)
        await self.bot.store.set(self.guild.id, "notices_enabled", False)
        self.assertFalse(await publish_due(self.bot, self.guild, datetime(2026, 11, 14, 10, tzinfo=timezone.utc)))

    async def test_notice_recovers_after_send_before_ledger(self):
        now = datetime(2026, 11, 12, 9, 1, tzinfo=timezone.utc)
        real_set = self.bot.store.set
        async def fail_notice(guild_id, key, value):
            if key.startswith("notice:"):
                raise RuntimeError("DB failure")
            return await real_set(guild_id, key, value)
        with patch.object(self.bot.store, "set", side_effect=fail_notice):
            with self.assertRaises(RuntimeError):
                await publish_due(self.bot, self.guild, now)
        self.assertFalse(await publish_due(self.bot, self.guild, now))
        self.assertEqual(len((await self.channel("announcements")).messages), 1)

    async def test_backup_can_restore_records(self):
        await self.bot.store.reserve(self.guild.id, 77, "feedback", {"제목": "sample", "내용": "record"})
        path = await backup(self.settings.database)
        with sqlite3.connect(path) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT owner_id FROM tickets").fetchone()[0], 77)

    async def test_slash_command_payloads_are_valid(self):
        group = DDHCommands(self.bot)
        payload = group.to_dict(self.bot.tree)
        self.assertEqual({c["name"] for c in payload["options"]}, {"setup", "plan", "panels", "notices", "status", "security"})
        self.assertLess(len(json.dumps(payload)), 10000)


class PureTests(unittest.TestCase):
    def test_security_rules_are_scoped_and_non_destructive(self):
        rules = specifications()
        self.assertEqual(len(rules), 3)
        self.assertTrue(all(name.startswith("DDH |") for name, _, _ in rules))
        invite_rule = rules[0][1]
        self.assertEqual(invite_rule.type, discord.AutoModRuleTriggerType.keyword)
        self.assertIn("discord.gg/", invite_rule.keyword_filter)
        self.assertNotIn("github.com/", invite_rule.keyword_filter)
        self.assertEqual(rules[1][1].type, discord.AutoModRuleTriggerType.spam)
        self.assertEqual(rules[2][1].mention_limit, 5)

    def test_kst_schedule_handles_day_boundaries_and_interval(self):
        self.assertIsNone(due_date(datetime(2026, 11, 12, 8, 59, tzinfo=timezone.utc), date(2026, 11, 12), 18, 2))
        self.assertEqual(due_date(datetime(2026, 11, 12, 9, tzinfo=timezone.utc), date(2026, 11, 12), 18, 2), date(2026, 11, 12))
        self.assertIsNone(due_date(datetime(2026, 11, 13, 10, tzinfo=timezone.utc), date(2026, 11, 12), 18, 2))

    def test_bad_payloads_cannot_inject_roles(self):
        with self.assertRaises(UserError):
            validate_payload("payment", {})
        with self.assertRaises(UserError):
            validate_payload("developer", {"_fields": ["staff"], "Roblox 사용자명·프로필": "x",
                                           "포트폴리오 링크": "https://example.com", "경력·도구·본인 담당 부분": "x"})
        self.assertEqual(len(FIELDS), 8)

    def test_config_fails_closed_without_new_guild(self):
        with patch.dict(os.environ, {"TOKEN": "test"}, clear=True), patch("config.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "DDH_GUILD_ID"):
                Settings.from_env()
        with patch.dict(os.environ, {"TOKEN": "test", "DDH_GUILD_ID": "123456789012345678"}, clear=True), patch("config.load_dotenv"):
            settings = Settings.from_env()
            self.assertEqual(settings.database, Path("data/ddh.db"))
            self.assertEqual(settings.notice_hour, 18)

    def test_overwrites_cannot_accidentally_leak_private_category(self):
        guild = Guild()
        staff = guild.make_role("staff")
        policy = overwrites(guild, {"staff": staff}, "private")
        self.assertFalse(policy[guild.default_role].view_channel)
        self.assertEqual(set(policy), {guild.default_role, guild.me, staff})
