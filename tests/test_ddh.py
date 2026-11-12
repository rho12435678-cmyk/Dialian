import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from config import Settings
from ddh.announcements import protect_announcement_channels
from ddh.backups import backup, restore_dry_run, verify_snapshot
from ddh.blueprint import CATEGORIES, CHANNELS, FIELDS, overwrites
from ddh.bot import DDHCommands, Dialian
from ddh.cleanup import cleanup_legacy
from ddh.content import (GUIDE_VERSION, guide, publish_guide, publish_panels,
                         repair_legacy_verification_panels, repair_missing_panels,
                         retire_update_alerts_panel)
from ddh.operations import inspect_automod, inspect_layout
from ddh.permissions import operator_migration
from ddh.verification import verify_member
from ddh.visibility import apply_visibility
from ddh.layout import LEGACY_MIGRATION_KEY, cosmetic_layout
from ddh.notices import OWNER_NOTICES_KEY, due_date, publish_due
from ddh.server import UserError, preflight, provision, roles_for
from ddh.security import rule_trigger_type, specifications
from ddh.store import Store
from ddh.tickets import validate_payload
from ddh.updates import publish_release, redact_legacy_release_links, release_embed
from ddh.views import ReportModal, RulesView, SupportView, TicketActions
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

    async def test_security_api_failure_reports_exact_stage_and_keeps_incomplete(self):
        self.guild.fetch_automod_rules = AsyncMock(side_effect=RuntimeError("API unavailable"))
        with self.assertRaisesRegex(UserError, "AutoMod 규칙 목록 조회"):
            await self.bot.apply_security(self.guild)
        self.assertFalse(await self.bot.store.get(self.guild.id, "security_complete"))
        self.assertIn(
            "AutoMod 규칙 목록 조회",
            await self.bot.store.get(self.guild.id, "security:last_error"),
        )

    async def test_security_rejects_exposed_log_channel_with_owner_hint(self):
        audit = await self.channel("security_log")
        await audit.edit(overwrites={
            self.guild.default_role: discord.PermissionOverwrite(view_channel=True),
            self.guild.me: discord.PermissionOverwrite(view_channel=True),
        })
        with self.assertRaisesRegex(UserError, "비공개 권한 불일치"):
            await self.bot.apply_security(self.guild)
        self.assertFalse(await self.bot.store.get(self.guild.id, "security_complete"))

    async def test_actual_automod_trigger_shape_does_not_break_health_checks(self):
        # discord.py AutoModRule from the live server has .trigger.type,
        # not necessarily .trigger_type. The old code crashed every minute.
        active_rules = [
            SimpleNamespace(
                name=name, trigger=SimpleNamespace(type=trigger.type),
                enabled=True,
                actions=[
                    discord.AutoModRuleAction(),
                    discord.AutoModRuleAction(
                        channel_id=await self.bot.store.get(self.guild.id, "channel:security_log")
                    ),
                ],
            )
            for name, trigger, _ in specifications()
        ]
        self.guild.fetch_automod_rules = AsyncMock(return_value=active_rules)
        self.assertIsNone(getattr(active_rules[0], "trigger_type", None))
        self.assertEqual(rule_trigger_type(active_rules[0]),
                         discord.AutoModRuleTriggerType.keyword)
        self.assertEqual(await inspect_automod(self.guild, self.bot.store), [])
        self.assertIsInstance(await self.bot.operations.check(self.guild), list)

    async def test_security_reuses_active_rule_without_trigger_type_attribute(self):
        existing = [SimpleNamespace(
            name="서버 기존 스팸 차단",
            trigger=SimpleNamespace(type=discord.AutoModRuleTriggerType.spam),
            enabled=True,
            actions=[discord.AutoModRuleAction()],
        )]
        self.guild.fetch_automod_rules = AsyncMock(side_effect=lambda: list(existing))

        async def create_rule(**kwargs):
            rule = SimpleNamespace(
                name=kwargs["name"],
                trigger=SimpleNamespace(type=kwargs["trigger"].type),
                enabled=True, actions=kwargs["actions"],
            )
            existing.append(rule)
            return rule

        self.guild.create_automod_rule = AsyncMock(side_effect=create_rule)
        result = await self.bot.apply_security(self.guild)
        self.assertTrue(await self.bot.store.get(self.guild.id, "security_complete"))
        self.assertIn("기존 활성 규칙 유지", " ".join(result))
        self.assertEqual(len(existing), 3)

    async def test_security_does_not_claim_success_with_disabled_existing_spam(self):
        rules = [SimpleNamespace(
            name="서버 기존 스팸 차단",
            trigger=SimpleNamespace(type=discord.AutoModRuleTriggerType.spam),
            enabled=False, actions=[],
        )]
        self.guild.fetch_automod_rules = AsyncMock(side_effect=lambda: list(rules))

        async def create_rule(**params):
            rule = SimpleNamespace(
                name=params["name"], trigger=SimpleNamespace(type=params["trigger"].type),
                enabled=params["enabled"], actions=params["actions"],
            )
            rules.append(rule)
            return rule

        self.guild.create_automod_rule = AsyncMock(side_effect=create_rule)
        with self.assertRaisesRegex(UserError, "기존 Discord 규칙이 비활성화"):
            await self.bot.apply_security(self.guild)
        self.assertFalse(await self.bot.store.get(self.guild.id, "security_complete"))

    async def test_security_applies_all_automod_rules_and_clears_error(self):
        rules = []
        self.guild.fetch_automod_rules = AsyncMock(side_effect=lambda: list(rules))

        async def create_rule(**params):
            rule = SimpleNamespace(
                name=params["name"], trigger=SimpleNamespace(type=params["trigger"].type),
                enabled=params["enabled"], actions=params["actions"],
            )
            rules.append(rule)
            return rule

        self.guild.create_automod_rule = AsyncMock(side_effect=create_rule)
        await self.bot.store.set(self.guild.id, "security:last_error", "이전 실패")
        result = await self.bot.apply_security(self.guild)
        self.assertEqual(len(result), 3)
        self.assertTrue(await self.bot.store.get(self.guild.id, "security_complete"))
        self.assertIsNone(await self.bot.store.get(self.guild.id, "security:last_error"))
        self.assertEqual(len(rules), 3)

    async def test_existing_operator_is_adopted_without_legacy_staff_duplication(self):
        operator = next(role for role in self.guild.roles if role.name == "운영자")
        self.assertEqual(self.roles["staff"].id, operator.id)
        previous = self.guild.make_role("운영진・Staff")
        await self.bot.store.set(self.guild.id, "role:staff", previous.id)
        await provision(self.guild, self.bot.store, self.settings)
        self.assertEqual(await self.bot.store.get(self.guild.id, "role:staff"), operator.id)
        self.assertEqual(len([role for role in self.guild.roles if role.name == "운영자"]), 1)
        self.assertIn("이전 운영진", " ".join(await inspect_layout(self.guild, self.bot.store)))

    async def test_owner_only_operator_permission_migration_is_conservative(self):
        old = self.guild.make_role("운영진・Staff")
        channel = await self.channel("audit")
        previous = dict(channel.overwrites)
        previous[old] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        custom = self.guild.make_role("무관한 맞춤역할")
        previous[custom] = discord.PermissionOverwrite(view_channel=False)
        await channel.edit(overwrites=previous)
        preview = await operator_migration(self.guild, self.bot.store, apply=False)
        self.assertIn("운영진・Staff", preview)
        self.assertIn(old, channel.overwrites)
        result = await operator_migration(self.guild, self.bot.store, apply=True)
        self.assertIn("변경 완료", result)
        self.assertNotIn(old, channel.overwrites)
        self.assertIn(self.roles["staff"], channel.overwrites)
        self.assertIn(custom, channel.overwrites)
        self.assertEqual(await self.bot.store.get(self.guild.id, "role:staff"), self.roles["staff"].id)
        self.assertIn(old, self.guild.roles)  # deletion is owner-controlled
        again = await operator_migration(self.guild, self.bot.store, apply=False)
        self.assertIn("0개", again)

    async def test_no_second_operator_is_created_when_owner_role_missing(self):
        guild = Guild(self.guild.id + 914)
        guild.roles = [role for role in guild.roles if role.name != "운영자"]
        count = len(guild.roles)
        with self.assertRaisesRegex(UserError, "운영자"):
            await provision(guild, self.bot.store, Settings("test", guild.id))
        self.assertEqual(len(guild.roles), count)

    async def test_private_report_flow_and_basic_validation(self):
        view = SupportView(self.bot)
        self.assertTrue(any(item.custom_id == "ddh:report:v1" for item in view.children))
        self.assertEqual(ReportModal(self.bot).title, "DDH 거래·도용 신고")
        report = await self.bot.tickets.create(
            self.guild, self.member, "feedback",
            {"제목": "[거래·작품 신고] 게시물 도용", "내용": "제작물 원본을 무단 게시한 것으로 신고"},
        )
        self.assertFalse(report.permissions_for(self.visitor).view_channel)
        self.assertTrue(report.permissions_for(self.staff).view_channel)
        stored = await self.bot.store.channel_ticket(self.guild.id, report.id)
        self.assertEqual(stored["kind"], "feedback")
        self.assertIn("거래·작품 신고", stored["fields"])

    async def test_backup_restore_dry_run_and_freshness(self):
        await self.bot.store.reserve(
            self.guild.id, 699, "feedback", {"제목": "verify", "내용": "test"}
        )
        self.assertIn("백업 없음", " ".join(self.bot.operations.backup_warnings()))
        snapshot = await backup(self.settings.database)
        self.assertTrue(verify_snapshot(snapshot))
        self.assertEqual(self.bot.operations.backup_warnings(), [])
        restored = restore_dry_run(snapshot, Path(self.tmp.name) / "restore-check.db")
        with sqlite3.connect(restored) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM tickets WHERE owner_id=699").fetchone()[0], 1
            )
        with self.assertRaises(FileExistsError):
            restore_dry_run(snapshot, restored)
        os.utime(snapshot, (0, 0))
        self.assertIn("백업 지연", " ".join(self.bot.operations.backup_warnings()))

    async def test_support_report_button_is_upgraded_once_automatically(self):
        await publish_panels(self.bot, self.guild)
        channel = await self.channel("support_panel")
        original = channel.messages[0]
        await self.bot.store.set(self.guild.id, "panels:version", "legacy")
        self.assertTrue(await repair_missing_panels(self.bot, self.guild))
        self.assertEqual(len(channel.messages), 1)
        self.assertEqual(channel.messages[0].id, original.id)
        self.assertTrue(any(
            item.custom_id == "ddh:report:v1"
            for item in channel.messages[0].view.children
        ))
        self.assertFalse(await repair_missing_panels(self.bot, self.guild))

    async def test_repair_deleted_guides_without_reposting_healthy_panels(self):
        self.assertTrue(await repair_missing_panels(self.bot, self.guild))
        self.assertFalse(await repair_missing_panels(self.bot, self.guild))
        self.assertEqual(len((await self.channel("rules")).messages), 1)
        guide_channel = await self.channel("guide")
        guide_channel.messages.clear()
        self.assertTrue(await repair_missing_panels(self.bot, self.guild))
        self.assertEqual(len(guide_channel.messages), 1)
        self.assertEqual(len((await self.channel("support_panel")).messages), 1)

    async def test_setup_keeps_owner_names_permissions_positions_and_ids(self):
        counts = len(self.guild.channels), len(self.guild.roles)
        channel = await self.channel("general")
        channel.name = "owner-chat"
        channel.position = 73
        policy = {self.guild.default_role: discord.PermissionOverwrite(send_messages=True)}
        await channel.edit(overwrites=policy)
        admin = self.roles["admin"]
        admin.name = "owner-custom-admin"
        admin.position = 230
        with patch.object(self.guild, "edit_role_positions", new_callable=AsyncMock) as order:
            await provision(self.guild, self.bot.store, self.settings)
        order.assert_not_awaited()
        self.assertEqual(counts, (len(self.guild.channels), len(self.guild.roles)))
        self.assertEqual((channel.name, channel.position, channel.overwrites), ("owner-chat", 73, policy))
        self.assertEqual((admin.name, admin.position), ("owner-custom-admin", 230))
        self.assertEqual(channel.id, (await self.channel("general")).id)

    async def test_startup_leaves_legacy_automation_disabled(self):
        await self.bot.store.set(self.guild.id, "notices_enabled", True)
        with (patch.object(self.bot, "get_guild", return_value=self.guild),
              patch.object(self.bot, "bootstrap", new_callable=AsyncMock) as setup,
              patch.object(self.bot, "apply_security", new_callable=AsyncMock) as security,
              patch("ddh.bot.publish_release", new_callable=AsyncMock) as release):
            await self.bot.on_ready()
        setup.assert_not_awaited()
        security.assert_not_awaited()
        release.assert_awaited_once_with(self.bot, self.guild)
        self.assertEqual(len((await self.channel("guide")).messages), 1)
        self.assertFalse(await publish_due(self.bot, self.guild, datetime(2026, 11, 12, 9, 1, tzinfo=timezone.utc)))

    async def test_server_mutations_require_actual_owner(self):
        group = DDHCommands(self.bot)
        administrator = self.guild.member(83, [self.roles["admin"]], admin=True)
        interaction = SimpleNamespace(guild=self.guild, user=administrator)
        for command in ("setup", "layout", "operator", "security", "panels", "verify", "notices", "cleanup", "visibility"):
            with self.subTest(command=command), self.assertRaises(UserError):
                kwargs = {"member": self.member} if command == "verify" else {"enabled": True} if command == "notices" else {}
                await getattr(group, command).callback(group, interaction, **kwargs)
        group.owner(SimpleNamespace(guild=self.guild, user=SimpleNamespace(id=self.guild.owner_id)))

    async def test_job_and_portfolio_channels_use_forum_types_and_tags(self):
        for key in ("hiring", "commissions", "portfolio"):
            channel = await self.channel(key)
            self.assertIsInstance(channel, discord.ForumChannel)
            self.assertGreaterEqual(len(channel.available_tags), 3)
        self.assertEqual((await self.channel("announcements")).type, discord.ChannelType.news)
        self.assertEqual((await self.channel("updates")).type, discord.ChannelType.news)

    async def test_forum_permissions_support_member_comments_not_fake_portfolios(self):
        forum = await self.channel("portfolio")
        viewer = self.guild.member(120, [self.roles["member"]])
        gfx = self.guild.member(121, [self.roles["member"], self.roles["developer"], self.roles["field:gfx"]])
        self.assertTrue(forum.permissions_for(viewer).view_channel)
        self.assertTrue(forum.permissions_for(viewer).send_messages_in_threads)
        self.assertFalse(forum.permissions_for(viewer).send_messages)
        self.assertTrue(forum.permissions_for(gfx).send_messages)
        self.assertFalse(forum.permissions_for(self.visitor).send_messages)
        commission = await self.channel("commissions")
        self.assertTrue(commission.permissions_for(viewer).send_messages)

    async def test_setup_adopts_existing_text_board_without_changing_its_type(self):
        guild = Guild(self.guild.id + 321)
        old = await guild.create_text_channel("구인")
        await old.send(embed=discord.Embed(title="기존 구인 글"))
        await provision(guild, self.bot.store, Settings("test", guild.id))
        new = guild.get_channel(await self.bot.store.get(guild.id, "channel:hiring"))
        self.assertIsInstance(new, discord.TextChannel)
        self.assertEqual(old.id, new.id)
        self.assertEqual(old.name, "구인")
        self.assertEqual(len(old.messages), 1)
        self.assertFalse(old.permissions_for(guild.member(500)).send_messages)
        count = len(guild.channels)
        await provision(guild, self.bot.store, Settings("test", guild.id))
        self.assertEqual(count, len(guild.channels))
        self.assertEqual(new.id, await self.bot.store.get(guild.id, "channel:hiring"))

    async def test_missing_news_feature_preserves_text_and_reports_pending(self):
        guild = Guild(self.guild.id + 322)
        guild.features = []
        old = await guild.create_text_channel("공지사항")
        await provision(guild, self.bot.store, Settings("test", guild.id))
        new = guild.get_channel(await self.bot.store.get(guild.id, "channel:announcements"))
        self.assertEqual(old.id, new.id)
        self.assertEqual(new.type, discord.ChannelType.text)
        self.assertTrue(await self.bot.store.get(guild.id, "news_format_pending"))
        self.assertIsInstance(guild.get_channel(await self.bot.store.get(guild.id, "channel:hiring")), discord.ForumChannel)
        guild.features = ["NEWS", "COMMUNITY"]
        await provision(guild, self.bot.store, Settings("test", guild.id))
        self.assertEqual(new.type, discord.ChannelType.text)
        self.assertTrue(await self.bot.store.get(guild.id, "news_format_pending"))

    async def test_compact_bilingual_layout_and_announce_support(self):
        self.assertEqual(len(CATEGORIES), 6)
        names = {ch.name for ch in CHANNELS}
        self.assertIn("포트폴리오-portfolio", names)
        self.assertIn("문의-support", names)
        self.assertNotIn("english-chat", names)
        self.assertEqual({ch.key for ch in CHANNELS if ch.forum and ch.category == "community"},
                         {"questions", "ideas"})
        self.assertEqual((await self.channel("support_panel")).type, discord.ChannelType.news)
        self.assertEqual((await self.channel("partners")).type, discord.ChannelType.news)
        self.assertEqual((await self.channel("portfolio")).type, discord.ChannelType.forum)
        self.assertFalse((await self.channel("portfolio")).permissions_for(self.member).send_messages)

    async def test_legacy_cleanup_requires_explicit_confirmation(self):
        old_tickets = await self.guild.create_category("🔐 티켓・Tickets")
        old_archive = await self.guild.create_category("📁 보관・Archive")
        await self.bot.store.set(self.guild.id, "category:tickets", old_tickets.id)
        await self.bot.store.set(self.guild.id, "category:archive", old_archive.id)
        old = await self.guild.create_text_channel("보관-잡담", category=old_archive)
        await old.send(embed=discord.Embed(title="영구삭제 전 기록"))
        ongoing = await self.bot.tickets.create(
            self.guild, self.member, "developer", self.payload()
        )
        await ongoing.edit(category=old_tickets)
        plan = await cleanup_legacy(
            self.guild, self.bot.store, self.settings, confirm=False
        )
        self.assertIn("삭제 대상", plan)
        self.assertIn(old, self.guild.channels)
        self.assertIn(ongoing, self.guild.channels)
        self.assertIn(old_archive, self.guild.channels)

        result = await cleanup_legacy(
            self.guild, self.bot.store, self.settings, confirm=True
        )
        self.assertIn("정리 완료", result)
        self.assertNotIn(old, self.guild.channels)
        self.assertNotIn(old_archive, self.guild.channels)
        self.assertNotIn(old_tickets, self.guild.channels)
        self.assertIn(ongoing, self.guild.channels)
        ops = await self.bot.store.get(self.guild.id, "category:ops")
        self.assertEqual(ongoing.category_id, ops)
        self.assertEqual(
            (await self.bot.store.channel_ticket(self.guild.id, ongoing.id))["state"],
            "open",
        )

    async def test_admin_role_and_top_community_rules_shortcut(self):
        admin = self.roles["admin"]
        self.assertTrue(admin.permissions.manage_channels)
        self.assertTrue(admin.permissions.manage_roles)
        self.assertFalse(admin.permissions.administrator)
        self.assertLess(self.roles["developer"], admin)
        self.assertLess(admin, self.roles["staff"])
        rules = await self.channel("rules")
        self.assertEqual(self.guild.rules_channel.id, rules.id)
        self.assertEqual([ch.key for ch in CHANNELS][:2], ["announcements", "updates"])

    async def test_active_ticket_in_staff_area_and_closed_channel_removed(self):
        channel = await self.bot.tickets.create(
            self.guild, self.member, "feedback", {"제목": "test", "내용": "hello"}
        )
        self.assertEqual(
            channel.category_id,
            await self.bot.store.get(self.guild.id, "category:ops"),
        )
        await self.bot.tickets.close(self.guild, channel, self.member, "resolved")
        self.assertNotIn(channel, self.guild.channels)
        record = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(record["state"], "closed")

    async def test_support_categories_follow_notice_categories(self):
        expected = ["info", "support", "community", "jobs"]
        self.assertEqual([category.key for category in CATEGORIES][:4], expected)

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
        self.assertEqual(len(guild.roles), 3)  # everyone, existing 운영자, Dialian bot

    async def test_missing_role_permission_blocks_creation_only(self):
        role = self.roles["field:gfx"]
        self.guild.roles.remove(role)
        self.guild.me.guild_permissions.manage_roles = False
        with self.assertRaises(UserError):
            await provision(self.guild, self.bot.store, self.settings)

    async def test_announcement_channels_block_posts_attachments_threads_for_everyone(self):
        staff = self.staff
        admin = self.guild.member(42, [self.roles["member"], self.roles["admin"]])
        developer = self.guild.member(
            43, [self.roles["member"], self.roles["developer"], self.roles["field:gfx"]],
        )
        other = self.guild.make_role(
            "커스텀 회원", permissions=discord.Permissions(
                send_messages=True, attach_files=True,
                create_public_threads=True, send_messages_in_threads=True,
            ),
        )
        custom = self.guild.member(44, [self.roles["member"], other])
        for spec in CHANNELS:
            if spec.policy != "readonly":
                continue
            with self.subTest(channel=spec.key):
                channel = await self.channel(spec.key)
                for person in (self.visitor, self.member, developer, custom):
                    effective = channel.permissions_for(person)
                    self.assertTrue(effective.view_channel)
                    for permission in (
                        "send_messages", "attach_files", "embed_links",
                        "add_reactions", "send_messages_in_threads",
                        "create_public_threads", "create_private_threads",
                        "create_polls", "manage_threads", "manage_webhooks",
                    ):
                        self.assertFalse(
                            getattr(effective, permission),
                            f"{spec.key} {person.id} can {permission}",
                        )
                self.assertTrue(channel.permissions_for(staff).send_messages)
                self.assertTrue(channel.permissions_for(admin).send_messages)
                self.assertTrue(channel.permissions_for(self.guild.me).send_messages)

    async def test_existing_announcement_permissions_self_heal_without_deleting_posts(self):
        channel = await self.channel("announcements")
        await channel.send(embed=discord.Embed(title="기존 공지 보존"))
        first = channel.messages[0]
        public = self.guild.make_role(
            "방문자 추가 역할", permissions=discord.Permissions(send_messages=True),
        )
        visitor = self.guild.member(50, [self.roles["member"], public])
        changed = dict(channel.overwrites)
        changed[public] = discord.PermissionOverwrite(
            send_messages=True, attach_files=True, create_public_threads=True,
        )
        await channel.edit(overwrites=changed)
        self.assertTrue(channel.permissions_for(visitor).send_messages)
        updated = await protect_announcement_channels(self.bot, self.guild)
        self.assertIn("announcements", updated)
        restored = channel.permissions_for(visitor)
        self.assertFalse(restored.send_messages)
        self.assertFalse(restored.attach_files)
        self.assertFalse(restored.create_public_threads)
        self.assertEqual(channel.messages[0].id, first.id)
        self.assertEqual(await protect_announcement_channels(self.bot, self.guild), [])
        self.assertEqual(len((await self.channel("updates")).messages), 0)

    async def test_old_verification_v1_panel_migrates_without_deleting_message(self):
        legacy_channel = await self.guild.create_text_channel("인증-verify")
        legacy = discord.Embed(
            title="DDH 멤버 인증", description="이전 인증 시스템 안내",
        )
        legacy.set_footer(text="DDH:verification:v1")
        outdated = discord.ui.View(timeout=None)
        outdated.add_item(discord.ui.Button(
            label="규칙 동의 · 멤버 인증", custom_id="ddh:verification:old",
        ))
        message = await legacy_channel.send(embed=legacy, view=outdated)
        original_id = message.id
        self.assertTrue(
            await repair_legacy_verification_panels(self.bot, self.guild)
        )
        self.assertEqual(len(legacy_channel.messages), 1)
        self.assertEqual(message.id, original_id)
        self.assertEqual(message.embeds[0].footer.text, "DDH:verification:v1")
        self.assertEqual(
            [item.custom_id for item in message.view.children],
            ["ddh:rules:v1"],
        )
        self.assertFalse(
            await repair_legacy_verification_panels(self.bot, self.guild)
        )
        self.assertEqual(
            await self.bot.store.get(self.guild.id, "message:verification"),
            original_id,
        )

    async def test_old_verification_not_migrated_if_written_by_other_bot(self):
        channel = await self.guild.create_text_channel("인증-verify")
        card = discord.Embed(title="인증")
        card.set_footer(text="DDH:verification:v1")
        message = await channel.send(embed=card)
        message.author = SimpleNamespace(id=999999)
        self.assertFalse(
            await repair_legacy_verification_panels(self.bot, self.guild)
        )
        self.assertIsNone(message.view)

    async def test_rules_view_acknowledges_before_database_checks(self):
        interaction = AsyncMock()
        interaction.guild = self.guild
        interaction.user = self.visitor
        original = self.bot.tickets.ready
        async def check_ack(guild):
            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True,
            )
            return await original(guild)
        with patch.object(self.bot.tickets, "ready", side_effect=check_ack):
            view = RulesView(self.bot)
            self.assertTrue(await view.interaction_check(interaction))
            await view.accept.callback(interaction)
        interaction.response.defer.assert_awaited_once()
        self.visitor.add_roles.assert_awaited_once()

    async def test_missing_live_verification_button_is_repaired_on_startup(self):
        await publish_panels(self.bot, self.guild)
        rules = await self.channel("rules")
        guide_channel = await self.channel("guide")
        rules_message = rules.messages[0]
        guide_message = guide_channel.messages[0]
        # The older logic accepted the embed footer even if the button had
        # gone missing; the new check must inspect live message components.
        rules_message.view = None
        guide_message.view = None
        self.assertTrue(await repair_missing_panels(self.bot, self.guild))
        self.assertEqual(rules.messages[0].id, rules_message.id)
        self.assertEqual(guide_channel.messages[0].id, guide_message.id)
        self.assertTrue(any(
            component.custom_id == "ddh:rules:v1"
            for component in rules_message.view.children
        ))
        self.assertTrue(any(
            component.custom_id == "ddh:rules:v1"
            for component in guide_message.view.children
        ))
        self.assertFalse(await repair_missing_panels(self.bot, self.guild))

    async def test_verification_allows_basic_existing_member_role_permissions(self):
        role = self.roles["member"]
        role._permissions = discord.Permissions(
            view_channel=True, read_message_history=True,
            use_application_commands=True,
        ).value
        self.assertTrue(await verify_member(self.bot, self.guild, self.visitor.id))
        self.assertIn(role, self.visitor.roles)

    async def test_verification_error_is_recorded_for_owner_and_success_clears(self):
        interaction = AsyncMock()
        interaction.guild = self.guild
        with patch("ddh.views.report_error", new_callable=AsyncMock):
            await RulesView(self.bot).on_error(
                interaction, UserError("멤버 역할 순서 확인 필요"), None,
            )
        self.assertIn(
            "역할 순서",
            await self.bot.store.get(self.guild.id, "verification:last_error"),
        )
        interaction.user = self.visitor
        interaction.channel_id = (await self.channel("rules")).id
        await RulesView(self.bot).accept.callback(interaction)
        self.assertIsNone(
            await self.bot.store.get(self.guild.id, "verification:last_error"),
        )

    async def test_official_verification_button_works_from_server_guide_location(self):
        interaction = AsyncMock()
        interaction.guild = self.guild
        interaction.user = self.visitor
        interaction.channel_id = 992211330012345678  # Server Guide resource view
        await RulesView(self.bot).accept.callback(interaction)
        self.visitor.add_roles.assert_awaited_once_with(
            self.roles["member"], reason="DDH 규칙 동의 및 계정 인증",
        )

    async def test_verification_rejects_underage_account_without_role_grant(self):
        self.visitor.created_at = datetime.now(timezone.utc) - timedelta(days=1)
        with self.assertRaisesRegex(UserError, "계정 생성 후"):
            await verify_member(self.bot, self.guild, self.visitor.id)
        self.visitor.add_roles.assert_not_awaited()

    async def test_one_time_visibility_only_exposes_rules_guide_verification(self):
        verification = await self.guild.create_text_channel("인증-verify")
        # Before the requested one-time change, unverified users still see chat.
        self.assertTrue((await self.channel("general")).permissions_for(self.visitor).view_channel)
        before = {channel.id: channel.overwrites for channel in self.guild.channels}
        preview = await apply_visibility(self.bot, self.guild)
        self.assertIn("규칙 · 가이드 · 인증-verify", preview)
        self.assertEqual(before, {
            channel.id: channel.overwrites for channel in self.guild.channels
        })
        result = await apply_visibility(self.bot, self.guild, apply=True)
        self.assertIn("완료", result)
        allowed = {
            (await self.channel("rules")).id,
            (await self.channel("guide")).id,
            verification.id,
        }
        non_member_role = self.guild.make_role(
            "외부 홍보 역할",
            permissions=discord.Permissions(view_channel=True),
        )
        visitor_with_role = self.guild.member(957, [non_member_role])
        for channel in self.guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue
            self.assertEqual(
                channel.permissions_for(self.visitor).view_channel,
                channel.id in allowed,
                f"미인증자에게 보이는 채널: {channel.name}",
            )
            self.assertEqual(
                channel.permissions_for(visitor_with_role).view_channel,
                channel.id in allowed,
                f"추가 역할만 보유한 미인증자에게 보이는 채널: {channel.name}",
            )
        self.assertTrue((await self.channel("general")).permissions_for(self.member).view_channel)
        self.assertFalse((await self.channel("audit")).permissions_for(self.member).view_channel)
        self.assertTrue((await self.channel("audit")).permissions_for(self.staff).view_channel)
        self.assertEqual(
            await self.bot.store.get(self.guild.id, "channel:verification"),
            verification.id,
        )
        self.assertIn("0개", await apply_visibility(self.bot, self.guild))

    async def test_owner_visibility_changes_are_not_reverted_by_background_jobs(self):
        verification = await self.guild.create_text_channel("인증")
        await apply_visibility(self.bot, self.guild, apply=True)
        updates = await self.channel("updates")
        # The owner intentionally makes a previously restricted channel
        # visible. The bot is not permitted to undo this manual decision.
        edited = dict(updates.overwrites)
        visitor_policy = discord.PermissionOverwrite(view_channel=True)
        edited[self.guild.default_role] = visitor_policy
        await updates.edit(overwrites=edited)
        before = updates.overwrites
        with (
            patch.object(self.bot, "get_guild", return_value=self.guild),
            patch("ddh.bot.publish_release", new_callable=AsyncMock),
            patch("ddh.bot.publish_due", new_callable=AsyncMock),
            patch.object(self.bot.tickets, "recover", new_callable=AsyncMock),
            patch.object(self.bot.operations, "catch_up", new_callable=AsyncMock),
            patch.object(self.bot.operations, "flush", new_callable=AsyncMock),
        ):
            await self.bot.on_ready()
            await self.bot.maintenance_once()
        self.assertEqual(updates.overwrites, before)
        self.assertTrue(updates.permissions_for(self.visitor).view_channel)
        self.assertFalse((await self.channel("general")).permissions_for(self.visitor).view_channel)
        self.assertTrue(verification.permissions_for(self.visitor).view_channel)

    async def test_non_public_custom_role_overwrite_cannot_bypass_visibility(self):
        verification = await self.guild.create_text_channel("인증")
        public = await self.channel("general")
        custom = self.guild.make_role("임시역할", permissions=discord.Permissions(view_channel=True))
        before = dict(public.overwrites)
        before[custom] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        await public.edit(overwrites=before)
        unverified = self.guild.member(903, [custom])
        self.assertTrue(public.permissions_for(unverified).view_channel)
        await apply_visibility(self.bot, self.guild, apply=True)
        self.assertFalse(public.permissions_for(unverified).view_channel)
        self.assertTrue(public.permissions_for(self.member).view_channel)
        self.assertTrue(public.permissions_for(self.staff).view_channel)
        self.assertTrue(verification.permissions_for(unverified).view_channel)

    async def test_ambiguous_verification_channel_refuses_to_mutate(self):
        await self.guild.create_text_channel("인증-one")
        await self.guild.create_text_channel("인증-two")
        before = {channel.id: channel.overwrites for channel in self.guild.channels}
        with self.assertRaisesRegex(UserError, "verification_channel"):
            await apply_visibility(self.bot, self.guild, apply=True)
        self.assertEqual(before, {
            channel.id: channel.overwrites for channel in self.guild.channels
        })

    async def test_public_permission_matrix_uses_discord_resolution(self):
        general = await self.channel("general")
        rules = await self.channel("rules")
        portfolio = await self.channel("portfolio")
        voice = await self.channel("lounge")
        gfx = self.guild.member(6, [self.roles["member"], self.roles["developer"], self.roles["field:gfx"]])
        vfx = self.guild.member(7, [self.roles["member"], self.roles["developer"], self.roles["field:vfx"]])
        self.assertTrue(general.permissions_for(self.visitor).view_channel)
        self.assertFalse(general.permissions_for(self.visitor).send_messages)
        self.assertTrue(general.permissions_for(self.member).send_messages)
        self.assertFalse(rules.permissions_for(self.member).send_messages)
        self.assertTrue(portfolio.permissions_for(vfx).send_messages)
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
        self.assertNotIn(channel, self.guild.channels)
        self.assertFalse(channel.permissions_for(self.member).view_channel)
        self.assertTrue(channel.permissions_for(self.staff).view_channel)

    async def test_rules_button_cannot_grant_a_modified_powerful_role(self):
        role = self.roles["member"]
        role._permissions = discord.Permissions(manage_roles=True).value
        interaction = AsyncMock()
        interaction.guild = self.guild
        interaction.channel_id = (await self.channel("rules")).id
        interaction.user = self.visitor
        with self.assertRaises(UserError):
            await RulesView(self.bot).accept.callback(interaction)
        self.visitor.add_roles.assert_not_called()

    async def test_public_guide_explains_purpose_and_first_steps(self):
        card = await guide(self.guild, self.bot.store)
        self.assertIn('Dial Dev Hub', card.title)
        values = '\n'.join(field.value for field in card.fields)
        self.assertIn('왜 만들어졌나요', '\n'.join(field.name for field in card.fields))
        self.assertIn('구인·구직', values)
        self.assertIn('포트폴리오', values)
        self.assertIn((await self.channel('rules')).mention, values)
        self.assertIn((await self.channel('support_panel')).mention, values)
        self.assertIn((await self.channel('hiring')).mention, values)
        self.assertIn((await self.channel('updates')).mention, values)
        self.assertLess(len(card), 6000)

    async def test_guide_refresh_is_idempotent(self):
        original = await publish_guide(self.bot, self.guild)
        self.assertEqual(await self.bot.store.get(self.guild.id, 'guide:version'), GUIDE_VERSION)
        refreshed = await publish_guide(self.bot, self.guild)
        self.assertEqual(original.id, refreshed.id)
        channel = await self.channel('guide')
        self.assertEqual(len(channel.messages), 1)
        self.assertEqual(channel.messages[0].embeds[0].footer.text, 'DDH:guide:v1')

    async def test_existing_guide_channel_name_is_preserved(self):
        guild = Guild(self.guild.id + 100)
        old_channel = await guild.create_text_channel('이용안내')
        await provision(guild, self.bot.store, Settings('test', guild.id))
        self.assertEqual(await self.bot.store.get(guild.id, 'channel:guide'), old_channel.id)
        self.assertEqual(old_channel.name, '이용안내')
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
        await self.bot.store.set(self.guild.id, OWNER_NOTICES_KEY, True)
        self.assertTrue(await publish_due(self.bot, self.guild, now))
        self.bot.store = Store(self.settings.database)
        self.assertFalse(await publish_due(self.bot, self.guild, now))
        self.assertEqual(len((await self.channel("announcements")).messages), 1)
        await self.bot.store.set(self.guild.id, OWNER_NOTICES_KEY, False)
        self.assertFalse(await publish_due(self.bot, self.guild, datetime(2026, 11, 14, 10, tzinfo=timezone.utc)))

    async def test_notice_recovers_after_send_before_ledger(self):
        await self.bot.store.set(self.guild.id, OWNER_NOTICES_KEY, True)
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

    async def test_update_release_announces_once_per_deployed_revision(self):
        self.bot.release_sha = "1" * 40
        self.assertTrue(await publish_release(self.bot, self.guild))
        channel = await self.channel("updates")
        self.assertEqual(len(channel.messages), 1)
        self.assertFalse(await publish_release(self.bot, self.guild))
        self.bot.release_sha = "2" * 40
        self.assertTrue(await publish_release(self.bot, self.guild))
        self.assertFalse(await publish_release(self.bot, self.guild))
        self.assertEqual(len(channel.messages), 2)
        self.assertEqual(channel.messages[0].content, self.roles["member"].mention)
        self.assertTrue(channel.messages[1].embeds[0].description.startswith(
            f"🔔 **업데이트 안내 · {self.roles['member'].mention}**"
        ))
        self.assertTrue(channel.messages[1].embeds[0].footer.text.startswith("Dial Dev Hub · #"))
        self.assertNotIn(self.bot.release_sha, channel.messages[1].embeds[0].footer.text)

    async def test_release_recovers_without_duplicate_when_db_save_fails(self):
        self.bot.release_sha = "3" * 40
        await publish_release(self.bot, self.guild)
        self.bot.release_sha = "4" * 40
        real_set = self.bot.store.set
        async def fail_record(guild_id, key, value):
            if key == "release:last_revision":
                raise RuntimeError("after-send write failed")
            return await real_set(guild_id, key, value)
        with patch.object(self.bot.store, "set", side_effect=fail_record):
            with self.assertRaises(RuntimeError):
                await publish_release(self.bot, self.guild)
        self.assertFalse(await publish_release(self.bot, self.guild))
        self.assertEqual(len((await self.channel("updates")).messages), 2)

    async def test_retires_old_signup_panel_preserving_actual_release_posts(self):
        channel = await self.channel("updates")
        card = discord.Embed(title="기존 업데이트 알림 신청")
        card.set_footer(text="DDH:update-alerts:v1")
        panel = await channel.send(embed=card)
        await self.bot.store.set(self.guild.id, "message:update-alerts", panel.id)
        self.bot.release_sha = "a" * 40
        self.assertTrue(await publish_release(self.bot, self.guild))
        unrelated = await channel.send(embed=discord.Embed(title="일반 공지"))
        self.assertTrue(await retire_update_alerts_panel(self.bot, self.guild))
        self.assertNotIn(panel, channel.messages)
        self.assertIn(unrelated, channel.messages)
        self.assertEqual(len(channel.messages), 2)
        self.assertFalse(await retire_update_alerts_panel(self.bot, self.guild))
        self.assertTrue(await self.bot.store.get(self.guild.id, "updates:panel_retired"))

    async def test_old_release_links_are_redacted_without_touching_other_notices(self):
        channel = await self.channel("updates")
        old = discord.Embed(
            title="기존 업데이트", description="https://github.com/example/repo",
        )
        old.set_footer(text="DDH:release:" + "e" * 40)
        legacy = await channel.send(embed=old)
        safe = await channel.send(embed=discord.Embed(title="일반 공지"))
        self.bot.release_sha = "e" * 40
        self.assertEqual(
            await redact_legacy_release_links(self.bot, self.guild), 1
        )
        visible = json.dumps(legacy.embeds[0].to_dict(), ensure_ascii=False).lower()
        self.assertNotIn("github.com", visible)
        self.assertNotIn("e" * 40, visible)
        self.assertIn(safe, channel.messages)
        self.assertEqual(
            await self.bot.store.get(self.guild.id, "release:last_revision"),
            "e" * 40,
        )
        self.assertEqual(
            await redact_legacy_release_links(self.bot, self.guild), 0
        )
        self.assertFalse(await publish_release(self.bot, self.guild))

    async def test_bot_startup_posts_release_without_slash_command(self):
        self.bot.release_sha = "b" * 40
        with patch.object(self.bot, "get_guild", return_value=self.guild):
            await self.bot.on_ready()
            await self.bot.on_ready()
        channel = await self.channel("updates")
        self.assertEqual(len(channel.messages), 1)
        self.assertEqual(channel.messages[0].content, self.roles["member"].mention)

    async def test_update_role_ping_permission_fixed_without_changing_member_perms(self):
        channel = await self.channel("updates")
        denied = channel.overwrites_for(self.guild.me)
        denied.mention_everyone = False
        await channel.set_permissions(self.guild.me, overwrite=denied)
        self.bot.release_sha = "c" * 40
        # The fake bot role has Administrator; override the effective check to
        # exercise the real Discord bot role's permission-repair branch.
        with patch.object(channel, "permissions_for", return_value=discord.Permissions.none()):
            self.assertTrue(await publish_release(self.bot, self.guild))
        self.assertFalse(channel.overwrites_for(self.guild.me).mention_everyone)
        self.assertFalse(channel.permissions_for(self.member).mention_everyone)

    async def test_layout_applies_only_requested_changes_and_never_reapplies(self):
        guide_channel = await self.channel("guide")
        await guide_channel.edit(type=discord.ChannelType.text, name="내-가이드")
        guide_policy = guide_channel.overwrites
        await guide_channel.send("기존 가이드 글")
        community = self.guild.get_channel(await self.bot.store.get(self.guild.id, "category:community"))
        ops = self.guild.get_channel(await self.bot.store.get(self.guild.id, "category:ops"))
        questions = await self.channel("questions")
        questions.name = "내-질문-포럼"
        old_question = await self.guild.create_text_channel("질문-help", category=community)
        idea = await self.channel("ideas")
        old_idea = await self.guild.create_text_channel("아이디어-ideas", category=community)
        resources = await self.guild.create_text_channel("자료-resources", category=community)
        await resources.send("삭제할 자료")
        hiring = await self.channel("hiring")
        hiring.name = "내-구인-포럼"
        preview = await cosmetic_layout(self.guild, self.bot.store)
        self.assertIn("Staff로 이동할", preview)
        self.assertIn("영구 삭제할 자료 채널 1개", preview)
        self.assertEqual(guide_channel.type, discord.ChannelType.text)
        self.assertEqual(resources.category_id, community.id)

        await cosmetic_layout(self.guild, self.bot.store, apply=True)
        self.assertEqual(guide_channel.type, discord.ChannelType.news)
        self.assertEqual(guide_channel.name, "내-가이드")
        self.assertEqual(guide_channel.overwrites, guide_policy)
        self.assertEqual(guide_channel.messages[0].content, "기존 가이드 글")
        self.assertEqual((await self.channel("questions")).id, questions.id)
        self.assertEqual(questions.name, "내-질문-포럼")
        self.assertEqual(hiring.name, "내-구인-포럼")
        self.assertEqual((await self.channel("ideas")).id, idea.id)
        self.assertIsInstance(await self.channel("ideas"), discord.ForumChannel)
        for old in (old_question, old_idea):
            self.assertEqual(old.category_id, ops.id)
            self.assertFalse(old.permissions_for(self.visitor).view_channel)
        self.assertNotIn(resources, self.guild.channels)
        self.assertIsNone(await self.bot.store.get(self.guild.id, "channel:resources"))

        await guide_channel.edit(type=discord.ChannelType.text)
        self.assertIn("이미 적용", await cosmetic_layout(self.guild, self.bot.store, apply=True))
        self.assertEqual(guide_channel.type, discord.ChannelType.text)

    async def test_layout_corrects_previous_migration_and_deletes_archived_resources(self):
        store = self.bot.store
        guild = self.guild
        await store.set(guild.id, LEGACY_MIGRATION_KEY, True)
        community = guild.get_channel(await store.get(guild.id, "category:community"))
        staff = guild.get_channel(await store.get(guild.id, "category:ops"))
        original_idea = await self.channel("ideas")
        await original_idea.edit(name="보관-아이디어-ideas", category=staff)
        replacement = await guild.create_text_channel("아이디어-ideas", category=community)
        await store.set(guild.id, "channel:ideas", replacement.id)
        old_resources = await guild.create_forum("보관-자료-resources", category=staff)
        new_resources = await guild.create_text_channel("자료-resources", category=community)
        await store.set(guild.id, "channel:resources", new_resources.id)
        unrelated = await guild.create_text_channel("내-작업자료", category=staff)
        guide_channel = await self.channel("guide")
        await guide_channel.edit(type=discord.ChannelType.text)

        await cosmetic_layout(guild, store, apply=True)

        self.assertEqual((await self.channel("ideas")).id, original_idea.id)
        self.assertEqual(original_idea.category_id, community.id)
        self.assertEqual(original_idea.name, "아이디어-ideas")
        self.assertTrue(original_idea.permissions_for(self.member).send_messages)
        self.assertEqual(replacement.category_id, staff.id)
        self.assertNotIn(old_resources, guild.channels)
        self.assertNotIn(new_resources, guild.channels)
        self.assertIn(unrelated, guild.channels)
        self.assertEqual(guide_channel.type, discord.ChannelType.text)

    async def test_member_screening_account_age_and_manual_exception(self):
        self.visitor.pending = True
        with self.assertRaises(UserError):
            await verify_member(self.bot, self.guild, self.visitor.id, reviewer=self.staff)
        self.visitor.pending = False
        self.visitor.created_at = datetime.now(timezone.utc) - timedelta(days=1)
        with self.assertRaises(UserError):
            await verify_member(self.bot, self.guild, self.visitor.id)
        self.visitor.add_roles.assert_not_called()
        self.assertTrue(await verify_member(self.bot, self.guild, self.visitor.id, reviewer=self.staff))
        self.assertIn(self.roles["member"], self.visitor.roles)
        self.assertFalse(await verify_member(self.bot, self.guild, self.visitor.id))
        self.assertIn("수동 인증", (await self.channel("audit")).messages[-1].embeds[0].description)

    async def test_member_events_queue_while_log_permissions_are_unsafe(self):
        audit = await self.channel("audit")
        self.assertTrue(audit.permissions_for(self.staff).view_channel)
        self.assertFalse(audit.permissions_for(self.staff).send_messages)
        self.assertFalse(audit.permissions_for(self.staff).manage_messages)
        self.assertFalse(audit.permissions_for(self.member).view_channel)
        original = audit.overwrites
        exposed = dict(original)
        exposed[self.guild.default_role] = discord.PermissionOverwrite(view_channel=True)
        await audit.edit(overwrites=exposed)
        await self.bot.on_member_join(self.visitor)
        with patch.object(self.bot, "get_guild", return_value=self.guild):
            await self.bot.on_raw_member_remove(SimpleNamespace(guild_id=self.guild.id, user=self.visitor))
        self.assertFalse(audit.messages)
        self.assertEqual(len(await self.bot.store.pending_events(self.guild.id, "audit")), 2)
        self.assertIn("채널 권한 불일치 · 로그-logs", await inspect_layout(self.guild, self.bot.store))
        await audit.edit(overwrites=original)
        await self.bot.operations.flush(self.guild)
        self.assertIn("입장", audit.messages[-1].embeds[0].description)
        self.assertIn("퇴장", audit.messages[-1].embeds[0].description)
        self.assertFalse(await self.bot.store.pending_events(self.guild.id, "audit"))

    async def test_audit_event_is_deduplicated_across_gateway_and_poll(self):
        entry = SimpleNamespace(guild=self.guild, id=901, user_id=self.staff.id,
                                action=discord.AuditLogAction.channel_delete,
                                target=discord.Object(id=902), created_at=datetime.now(timezone.utc))
        await self.bot.on_audit_log_entry_create(entry)
        async def history(**kwargs):
            yield entry
        self.guild.audit_logs = history
        await self.bot.operations.catch_up(self.guild)
        await self.bot.operations.flush(self.guild)
        self.assertEqual(await self.bot.store.get(self.guild.id, "audit:cursor"), entry.id)
        log_channel = await self.channel("security_log")
        self.assertEqual(len(log_channel.messages), 1)
        self.assertIn("채널 삭제", log_channel.messages[0].embeds[0].description)

    async def test_existing_ticket_blocks_resending_without_overriding_owner_permissions(self):
        payload = self.payload()
        channel = await self.bot.tickets.create(self.guild, self.member, "developer", payload)
        await channel.edit(overwrites={self.guild.default_role: discord.PermissionOverwrite(view_channel=True)})
        with self.assertRaises(UserError):
            await self.bot.tickets.create(self.guild, self.member, "developer", payload)
        self.assertTrue(channel.permissions_for(self.visitor).view_channel)

    async def test_backup_can_restore_records(self):
        await self.bot.store.reserve(self.guild.id, 77, "feedback", {"제목": "sample", "내용": "record"})
        path = await backup(self.settings.database)
        with sqlite3.connect(path) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT owner_id FROM tickets").fetchone()[0], 77)

    async def test_slash_command_payloads_are_valid(self):
        group = DDHCommands(self.bot)
        payload = group.to_dict(self.bot.tree)
        self.assertEqual({c["name"] for c in payload["options"]}, {"setup", "plan", "panels", "notices", "status", "security", "cleanup", "check", "verify", "layout", "operator", "visibility"})
        self.assertLess(len(json.dumps(payload)), 10000)


class PureTests(unittest.TestCase):
    def test_release_embed_has_no_source_account_link_or_commit_hash(self):
        revision = "2" * 40
        with patch("ddh.updates._git", return_value="ddh/updates.py"):
            card = release_embed("1" * 40, revision)
        visible = json.dumps(card.to_dict(), ensure_ascii=False).lower()
        self.assertNotIn("github", visible)
        self.assertNotIn("rho12435678", visible)
        self.assertNotIn("https://", visible)
        self.assertNotIn(revision, visible)
        self.assertIn("업데이트 공지 자동화", visible)

    def test_release_notes_never_repeat_untrusted_commit_messages(self):
        revision = "4" * 40
        source = "account-name : @everyone https://github.com/example"
        with patch("ddh.updates._git", return_value=source):
            card = release_embed("3" * 40, revision)
        self.assertNotIn("account-name", card.fields[0].value)
        self.assertNotIn("@everyone", card.fields[0].value)
        self.assertNotIn("github", card.fields[0].value.lower())

    def test_security_rules_are_scoped_and_non_destructive(self):
        rules = specifications()
        self.assertEqual(len(rules), 3)
        self.assertTrue(all(name.startswith("DDH |") for name, _, _ in rules))
        invite_rule = rules[0][1]
        self.assertEqual(invite_rule.type, discord.AutoModRuleTriggerType.keyword)
        self.assertIn("*discord.gg/*", invite_rule.keyword_filter)
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
        admin = guild.make_role("admin")
        policy = overwrites(guild, {"staff": staff, "admin": admin}, "private")
        self.assertFalse(policy[guild.default_role].view_channel)
        self.assertEqual(set(policy), {guild.default_role, guild.me, staff, admin})
