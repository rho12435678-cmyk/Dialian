import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from config import Settings
from ddh.bot import Dialian
from ddh.blueprint import FIELDS, access
from ddh.commissions import (assign, form_labels, install, progress, quote, save_account, validate_commission)
from ddh.commission_views import CommissionModal, CommissionActions
from ddh.operations import inspect_layout
from ddh.server import UserError, provision, roles_for
from ddh.store import Store
from ddh.views import TicketActions
from fakes import Guild


class CommissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guild = Guild()
        self.bot = Dialian(Settings('test', self.guild.id, Path(self.tmp.name) / 'ddh.db'))
        self.addAsyncCleanup(self.bot.close)
        await self.bot.store.initialize()
        await provision(self.guild, self.bot.store, self.bot.settings)
        await self.bot.store.set(self.guild.id, 'setup_complete', True)
        self.roles = await roles_for(self.guild, self.bot.store)
        self.member = self.guild.member(21, [self.roles['member']])
        self.staff = self.guild.member(22, [self.roles['staff']])
        self.gfx = self.guild.member(23, [self.roles['member'], self.roles['developer'], self.roles['field:gfx']])
        self.other = self.guild.member(24, [self.roles['member'], self.roles['developer'], self.roles['field:ui']])
        self.visitor = self.guild.member(25)
        await install(self.bot, self.guild)

    def payload(self, field='gfx'):
        return {'_field': field, **{label: '예시 요구사항' for label in form_labels(field)}}

    async def ticket(self):
        return await self.bot.tickets.create(self.guild, self.member, 'commission', self.payload())

    async def test_assignment_retry_revokes_previous_developer_and_stale_quote(self):
        channel = await self.ticket()
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        await save_account(self.bot, self.guild, self.gfx, '은행', '123456', '이름')
        await quote(self.bot, self.guild, channel, self.gfx, '1000', '이전 견적')
        replacement = self.guild.member(26, [self.roles['member'], self.roles['field:gfx']])
        with patch.object(channel, 'edit', side_effect=RuntimeError('REST failed')):
            with self.assertRaises(RuntimeError):
                await assign(self.bot, self.guild, channel, self.staff, replacement)
        # Former assignee cannot publish payment instructions during retry.
        with self.assertRaises(UserError):
            await quote(self.bot, self.guild, channel, self.gfx, '1000', '이전 견적')
        await assign(self.bot, self.guild, channel, self.staff, replacement)
        self.assertFalse(channel.permissions_for(self.gfx).view_channel)
        self.assertTrue(channel.permissions_for(replacement).view_channel)
        self.assertNotIn('123456', str([m.embeds[0].to_dict() for m in channel.messages]))

    async def test_forms_cover_eight_fields_and_validate_tampering(self):
        self.assertEqual(len(FIELDS), 8)
        labels = set()
        for field in FIELDS:
            form = CommissionModal(self.bot, field)
            self.assertEqual(len(form.children), 5)
            self.assertLessEqual(len(form.title), 45)
            self.assertTrue(all(len(item.label) <= 45 for item in form.children))
            labels.add(form.children[1].label)
            validate_commission(self.payload(field))
        self.assertEqual(len(labels), 8)
        for payload in ({'_field': 'unknown'}, {'_field': 'gfx'}, {**self.payload(), 'extra': 'x'}):
            with self.assertRaises(UserError):
                validate_commission(payload)
        self.assertTrue(CommissionActions(self.bot).is_persistent())

    async def test_workflow_private_account_quote_progress_and_closed_archive(self):
        channel = await self.ticket()
        self.assertFalse(channel.permissions_for(self.gfx).view_channel)
        self.assertTrue(channel.permissions_for(self.member).view_channel)
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        self.assertTrue(channel.permissions_for(self.gfx).view_channel)
        for member in (self.other, self.visitor):
            self.assertFalse(channel.permissions_for(member).view_channel)
        await save_account(self.bot, self.guild, self.gfx, '테스트은행', '123-456-7890', '예금주')
        await quote(self.bot, self.guild, channel, self.gfx, '10,000', '이미지 1장 · 7일 · 수정 2회')
        await quote(self.bot, self.guild, channel, self.gfx, '12,000', '이미지 1장 · 7일 · 수정 3회')
        quotes = [m for m in channel.messages if '견적 및 입금 안내' in m.embeds[0].title]
        self.assertEqual(len(quotes), 1)
        self.assertIn('123-456-7890', quotes[0].embeds[0].description)
        self.assertIn('12,000원', quotes[0].embeds[0].description)
        for stage in ('working', 'revision', 'delivered'):
            await progress(self.bot, self.guild, channel, self.gfx, stage)
        await self.bot.tickets.close(self.guild, channel, self.gfx, '납품 완료 확인')
        saved = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(saved['state'], 'closed')
        self.assertIn(channel, self.guild.channels)
        self.assertTrue(channel.permissions_for(self.member).view_channel)
        self.assertFalse(channel.permissions_for(self.member).send_messages)
        self.assertFalse(channel.permissions_for(self.gfx).send_messages)
        with self.assertRaises(UserError):
            await progress(self.bot, self.guild, channel, self.gfx, 'working')
        # Sensitive bank data only exists in the private quote and account table.
        async with self.bot.store.connect() as db:
            logs = await (await db.execute('SELECT body FROM operations_log')).fetchall()
        self.assertNotIn('123-456-7890', str([row[0] for row in logs]))

    async def test_requester_and_unassigned_developer_cannot_close_or_manage(self):
        channel = await self.ticket()
        for actor in (self.member, self.gfx, self.other, self.visitor):
            with self.assertRaises(UserError):
                await self.bot.tickets.close(self.guild, channel, actor, '거부되어야 함')
            with self.assertRaises(UserError):
                await assign(self.bot, self.guild, channel, actor, self.gfx)
        with self.assertRaises(UserError):
            await assign(self.bot, self.guild, channel, self.staff, self.other)
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        await self.gfx.remove_roles(self.roles['field:gfx'])
        with self.assertRaises(UserError):
            await self.bot.tickets.close(self.guild, channel, self.gfx, '역할 회수 이후')
        await self.bot.tickets.close(self.guild, channel, self.staff, '관리자 종료')

    async def test_support_old_close_buttons_also_reject_requester(self):
        channel = await self.bot.tickets.create(self.guild, self.member, 'feedback', {'제목': '문의', '내용': '테스트'})
        view = TicketActions(self.bot)
        interaction = SimpleNamespace(guild=self.guild, channel_id=channel.id, user=self.member,
                                      response=SimpleNamespace(send_modal=AsyncMock()))
        with self.assertRaises(UserError):
            await view.launch(interaction, 'close')
        interaction.response.send_modal.assert_not_awaited()
        with self.assertRaises(UserError):
            await self.bot.tickets.close(self.guild, channel, self.gfx, '다른 문의는 권한 없음')
        await self.bot.tickets.close(self.guild, channel, self.staff, '운영진 종료')
        self.assertNotIn(channel, self.guild.channels)

    async def test_account_authority_guild_scope_and_exposed_ticket_block(self):
        channel = await self.ticket()
        with self.assertRaises(UserError):
            await save_account(self.bot, self.guild, self.member, '은행', '123456', '이름')
        with self.assertRaises(UserError):
            await save_account(self.bot, self.guild, self.gfx, '은행', 'invalid', '이름')
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        with self.assertRaises(UserError):
            await quote(self.bot, self.guild, channel, self.gfx, '1000', '조건')
        await save_account(self.bot, self.guild, self.gfx, '은행', '123456', '이름')
        self.assertIsNone(await self.bot.store.account(self.guild.id + 1, self.gfx.id))
        for amount in ('-1', '0', 'nan', '100000001'):
            with self.assertRaises(UserError):
                await quote(self.bot, self.guild, channel, self.gfx, amount, '조건')
        policy = channel.overwrites
        policy[self.other] = access()
        await channel.edit(overwrites=policy)
        with self.assertRaises(UserError):
            await quote(self.bot, self.guild, channel, self.gfx, '1000', '조건')
        self.assertFalse(any('견적 및 입금 안내' in m.embeds[0].title for m in channel.messages))

    async def test_concurrent_requests_and_restart_preserve_assignment(self):
        channels = await asyncio.gather(*[self.ticket() for _ in range(3)])
        self.assertEqual(len({c.id for c in channels}), 1)
        channel = channels[0]
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        await save_account(self.bot, self.guild, self.gfx, '은행', '123456', '이름')
        restarted = Dialian(self.bot.settings)
        self.addAsyncCleanup(restarted.close)
        await restarted.store.initialize()
        await restarted.tickets.recover(self.guild)
        self.assertEqual((await restarted.store.channel_ticket(self.guild.id, channel.id))['assigned_id'], self.gfx.id)
        self.assertEqual((await restarted.store.account(self.guild.id, self.gfx.id))['account'], '123456')
        self.assertFalse(await restarted.store.get(self.guild.id, 'release:enabled', False))
        await restarted.store.delete_account(self.guild.id, self.gfx.id)
        self.assertIsNone(await restarted.store.account(self.guild.id, self.gfx.id))

    async def test_explicit_install_archives_forum_and_does_not_notify_updates(self):
        old = await self.guild.create_forum('의뢰-commissions')
        await self.bot.store.set(self.guild.id, 'channel:commissions', old.id)
        await install(self.bot, self.guild)
        self.assertIn(old, self.guild.channels)
        self.assertTrue(old.name.startswith('보관-'))
        self.assertTrue(old.permissions_for(self.member).view_channel)
        self.assertFalse(old.permissions_for(self.member).send_messages)
        self.assertFalse(old.permissions_for(self.member).send_messages_in_threads)
        support = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:support_panel'))
        self.assertEqual(len(support.messages), 1)
        self.assertIn('ddh:commission:v1', {i.custom_id for i in support.messages[0].view.children})
        updates = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:updates'))
        self.assertEqual(updates.messages, [])

    async def test_assigned_member_does_not_trigger_ticket_leak_warning(self):
        channel = await self.ticket()
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        self.assertFalse(any('티켓 접근 범위' in s for s in await inspect_layout(self.guild, self.bot.store)))

    async def test_logs_distinguish_actor_change_reason_and_security(self):
        channel = await self.ticket()
        entry = SimpleNamespace(guild=self.guild, id=4545, user_id=self.staff.id, user=self.staff,
            action=discord.AuditLogAction.channel_update, target=channel, reason='운영자 요청',
            before=SimpleNamespace(name='기존 이름'), after=SimpleNamespace(name='새 이름'),
            created_at=datetime.now(timezone.utc))
        await self.bot.operations.audit(entry)
        log = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:audit'))
        card = log.messages[-1].embeds[0]
        self.assertIn('운영 활동', card.title)
        self.assertIn('기존 이름 → 새 이름', card.description)
        self.assertIn(self.staff.name, card.description)
        self.assertIn('운영자 요청', card.description)
        self.assertNotIn('4545', card.description)
        self.assertIn('4545', card.footer.text)
        entry.id = 4546
        entry.action = discord.AuditLogAction.overwrite_update
        entry.before = SimpleNamespace(allow=discord.Permissions.none())
        entry.after = SimpleNamespace(allow=discord.Permissions(manage_roles=True))
        await self.bot.operations.audit(entry)
        security = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:security_log'))
        self.assertIn('역할 관리', security.messages[-1].embeds[0].description)
        self.assertEqual(len(log.messages), 2)  # ticket creation plus ordinary rename


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_schema_keeps_ids_reviews_settings_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.db'
            with sqlite3.connect(path) as db:
                db.executescript("""CREATE TABLE tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('developer','partner','feedback')),
                    fields TEXT NOT NULL, channel_id INTEGER UNIQUE, state TEXT NOT NULL DEFAULT 'creating',
                    decision TEXT NOT NULL DEFAULT 'pending', reviewer_id INTEGER, reason TEXT, close_reason TEXT,
                    closed_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    INSERT INTO tickets VALUES(42,1,2,'developer','{}',99,'open','approving',3,'승인 이유',NULL,NULL,'now','now');
                """)
            store = Store(path)
            await store.initialize()
            old = await store.ticket(42, 1)
            self.assertEqual((old['channel_id'], old['decision'], old['reason']), (99, 'approving', '승인 이유'))
            new = await store.reserve(1, 2, 'commission', {'_field': 'gfx'})
            self.assertGreater(new['id'], 42)
            with self.assertRaises(sqlite3.IntegrityError):
                await store.reserve(1, 2, 'commission', {'_field': 'ui'})
            await store.initialize()
            self.assertEqual((await store.ticket(new['id'], 1))['stage'], 'received')
