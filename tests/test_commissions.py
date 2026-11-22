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
from ddh.commissions import (assign, available_developers, form_labels, install, progress, quote, save_account, validate_commission)
from ddh.commission_views import (CommissionModal, CommissionActions, CommissionFieldView,
                                  CommissionDeveloperView, CommissionDeveloperSelect, CommissionEntryView)
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
        # These fixtures represent an installation after the requested copy rollout,
        # with this edition's optional announcement explicitly paused.
        await self.bot.store.set(self.guild.id, 'requested-copy:1.4.0', True)
        await self.bot.store.set(self.guild.id, 'release:1.5.0:enabled', False)
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

    async def test_all_forms_have_only_three_fields_with_optional_references(self):
        for field in FIELDS:
            modal = CommissionModal(self.bot, field)
            self.assertEqual(len(modal.children), 3)
            self.assertEqual([item.required for item in modal.children], [True, True, False])
            labels = {item.label for item in modal.children}
            self.assertNotIn('예산·희망 납기', labels)
            self.assertNotIn('사용 범위·추가 요청', labels)
            payload = self.payload(field)
            payload[form_labels(field)[-1]] = ''
            validate_commission(payload)
            payload[form_labels(field)[1]] = ''
            with self.assertRaises(UserError):
                validate_commission(payload)

    async def test_hub_has_instructions_pricing_and_members_only_read_access(self):
        hub = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:commission_panel'))
        category = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'category:commissions'))
        self.assertEqual(hub.category_id, category.id)
        self.assertTrue(hub.permissions_for(self.member).view_channel)
        self.assertFalse(hub.permissions_for(self.member).send_messages)
        self.assertFalse(hub.permissions_for(self.visitor).view_channel)
        self.assertFalse(category.permissions_for(self.visitor).view_channel)
        from ddh.announcements import protect_announcement_channels
        await protect_announcement_channels(self.bot, self.guild)
        self.assertFalse(hub.permissions_for(self.visitor).view_channel)
        card = hub.messages[0].embeds[0]
        text = str(card.to_dict())
        for label in ('신청 방법', '가격 안내', '접수 후 진행', '이용 안내'):
            self.assertIn(label, text)
        self.assertIn('공통 고정 가격표는 없습니다', text)
        self.assertLess(len(card), 6000)
        self.assertTrue(CommissionEntryView(self.bot).is_persistent())
        support = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:support_panel'))
        self.assertIn(hub.mention, support.messages[0].embeds[0].description)
        self.assertNotIn('ddh:commission:v1', {item.custom_id for item in support.messages[0].view.children})

    async def test_hub_install_moves_only_active_commissions_without_syncing_private_permissions(self):
        channel = await self.ticket()
        await assign(self.bot, self.guild, channel, self.staff, self.gfx)
        old_category = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'category:ops'))
        await channel.edit(category=old_category)
        note = await channel.send('기존 의뢰 대화')
        policy = channel.overwrites
        support_ticket = await self.bot.tickets.create(self.guild, self.other, 'feedback', {'제목': '문의', '내용': '도움'})
        with patch.object(channel, 'edit', wraps=channel.edit) as edit:
            await install(self.bot, self.guild)
        self.assertFalse(edit.call_args.kwargs['sync_permissions'])
        self.assertEqual(channel.overwrites, policy)
        self.assertIn(note, channel.messages)
        self.assertEqual(channel.category_id, await self.bot.store.get(self.guild.id, 'category:commissions'))
        self.assertEqual(support_ticket.category_id, old_category.id)
        self.assertFalse(channel.permissions_for(self.other).view_channel)
        self.assertTrue(channel.permissions_for(self.gfx).view_channel)

    async def test_missing_hub_is_created_once_and_restart_preserves_owner_changes(self):
        hub = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:commission_panel'))
        category = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'category:commissions'))
        self.guild.channels.remove(hub)
        self.guild.channels.remove(category)
        await self.bot.store.set(self.guild.id, 'channel:commission_panel', None)
        await self.bot.store.set(self.guild.id, 'category:commissions', None)
        await install(self.bot, self.guild)
        count = len(self.guild.channels)
        hub = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:commission_panel'))
        hub.name = '소유자가-꾸민-의뢰안내'
        await install(self.bot, self.guild)
        self.assertEqual(len(self.guild.channels), count)
        self.assertEqual(len(hub.messages), 1)
        self.assertEqual(hub.name, '소유자가-꾸민-의뢰안내')
        with patch.object(self.bot, 'get_guild', return_value=self.guild):
            await self.bot.on_ready()
        self.assertEqual(hub.name, '소유자가-꾸민-의뢰안내')
        self.assertEqual(len(hub.messages), 1)
        updates = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:updates'))
        self.assertEqual(updates.messages, [])

    async def test_old_support_button_redirects_to_hub(self):
        support = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:support_panel'))
        hub = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:commission_panel'))
        interaction = SimpleNamespace(guild=self.guild, channel_id=support.id, user=self.member)
        view = CommissionEntryView(self.bot)
        with patch('ddh.commission_views.reply', new_callable=AsyncMock) as response:
            await view.commission.callback(interaction)
        self.assertIn(hub.mention, response.call_args.args[1])

    async def test_field_picker_lists_two_gfx_developers_from_rest_then_opens_form(self):
        second = self.guild.member(31, [self.roles['member'], self.roles['field:gfx']])
        unverified = self.guild.member(32, [self.roles['field:gfx']])
        self.guild.member(33, [self.roles['member'], self.roles['developer']])
        await self.bot.store.set(self.guild.id, 'commissions:enabled', False)
        candidates = await available_developers(self.bot, self.guild, 'gfx', requester_id=self.member.id)
        self.assertEqual({member.id for member in candidates}, {self.gfx.id, second.id})
        self.assertNotIn(unverified, candidates)
        view = CommissionFieldView(self.bot, self.member.id)
        selector = view.children[0]
        selector._values = ['gfx']
        interaction = SimpleNamespace(guild=self.guild, user=self.member,
            response=SimpleNamespace(defer=AsyncMock(), send_modal=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        await selector.callback(interaction)
        picker = interaction.followup.send.call_args.kwargs['view']
        self.assertIsInstance(picker, CommissionDeveloperView)
        select = next(item for item in picker.children if isinstance(item, CommissionDeveloperSelect))
        self.assertEqual({option.value for option in select.options}, {str(self.gfx.id), str(second.id)})
        select._values = [str(second.id)]
        await select.callback(interaction)
        form = interaction.response.send_modal.call_args.args[0]
        self.assertEqual((form.field, form.developer_id), ('gfx', second.id))

    async def test_selected_developer_access_and_form_contents_without_setup_flag(self):
        await self.bot.store.set(self.guild.id, 'commissions:enabled', False)
        payload = {**self.payload(), '_developer': self.gfx.id}
        channel = await self.bot.tickets.create(self.guild, self.member, 'commission', payload)
        ticket = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual((ticket['assigned_id'], ticket['stage']), (self.gfx.id, 'assigned'))
        for member in (self.member, self.gfx):
            effective = channel.permissions_for(member)
            self.assertTrue(effective.view_channel and effective.send_messages and effective.attach_files and effective.read_message_history)
        self.assertFalse(channel.permissions_for(self.other).view_channel)
        self.assertFalse(channel.permissions_for(self.visitor).view_channel)
        form = channel.messages[0].embeds[0]
        self.assertIn('예시 요구사항', str(form.to_dict()))
        self.assertFalse(any(field.name == '_developer' for field in form.fields))
        self.assertIn(f'<@{self.gfx.id}>', channel.messages[1].content)
        self.assertIsNone(await self.bot.store.account(self.guild.id, self.gfx.id))  # bank is not an intake prerequisite
        with self.assertRaises(UserError):
            await self.bot.tickets.close(self.guild, channel, self.member, '고객은 종료 불가')
        await self.bot.tickets.close(self.guild, channel, self.gfx, '개발자 처리')

    async def test_developer_role_rechecked_after_selection_before_reserving(self):
        await self.gfx.remove_roles(self.roles['field:gfx'])
        for selection in (self.gfx.id, self.other.id, self.member.id, 999999):
            with self.assertRaises(UserError):
                await self.bot.tickets.create(self.guild, self.member, 'commission', {**self.payload(), '_developer': selection})
        self.assertIsNone(await self.bot.store.active(self.guild.id, self.member.id, 'commission'))
        for selection in (True, '23', -1):
            with self.assertRaises(UserError):
                validate_commission({**self.payload(), '_developer': selection})

    async def test_picker_pages_and_no_staff_assignment_when_roster_request_fails(self):
        developers = [self.guild.member(100 + i, [self.roles['member'], self.roles['field:gfx']]) for i in range(26)]
        picker = CommissionDeveloperView(self.bot, self.member.id, 'gfx', developers)
        interaction = SimpleNamespace(user=self.member, response=SimpleNamespace(edit_message=AsyncMock()))
        self.assertEqual(len(next(i for i in picker.children if isinstance(i, CommissionDeveloperSelect)).options), 25)
        await picker.turn_page(interaction, 1)
        self.assertEqual(len(next(i for i in picker.children if isinstance(i, CommissionDeveloperSelect)).options), 1)
        self.assertTrue(picker.next_page.disabled)
        empty = CommissionDeveloperView(self.bot, self.member.id, 'vfx', [])
        self.assertFalse(any(isinstance(item, CommissionDeveloperSelect) for item in empty.children))
        self.assertIn('운영진', empty.prompt())
        async def failing_members(**kwargs):
            raise discord.ClientException('intent unavailable')
            yield
        with patch.object(self.guild, 'fetch_members', failing_members):
            with self.assertRaisesRegex(UserError, 'Server Members Intent'):
                await available_developers(self.bot, self.guild, 'gfx', requester_id=self.member.id)

    async def test_chosen_developer_leaving_after_reservation_becomes_staff_pending(self):
        reserved = await self.bot.store.reserve(self.guild.id, self.member.id, 'commission',
            {**self.payload(), '_developer': self.gfx.id}, assigned_id=self.gfx.id)
        self.guild.members.pop(self.gfx.id)
        await self.bot.tickets.recover(self.guild)
        ticket = await self.bot.store.ticket(reserved['id'], self.guild.id)
        self.assertEqual((ticket['state'], ticket['assigned_id'], ticket['stage']), ('open', None, 'received'))
        channel = self.guild.get_channel(ticket['channel_id'])
        self.assertTrue(channel.permissions_for(self.member).view_channel)
        self.assertFalse(channel.permissions_for(self.other).view_channel)

    async def test_selected_developer_survives_interrupted_creation_without_duplicate_ping(self):
        original = self.bot.store.update_ticket
        async def fail_open(ticket_id, guild_id, **values):
            if values.get('state') == 'open':
                raise RuntimeError('interrupted after posting forms')
            return await original(ticket_id, guild_id, **values)
        with patch.object(self.bot.store, 'update_ticket', side_effect=fail_open):
            with self.assertRaises(RuntimeError):
                await self.bot.tickets.create(self.guild, self.member, 'commission', {**self.payload(), '_developer': self.gfx.id})
        pending = await self.bot.store.active(self.guild.id, self.member.id, 'commission')
        self.assertEqual(pending['assigned_id'], self.gfx.id)
        channel = self.guild.get_channel(pending['channel_id'])
        with patch.object(channel, 'send', wraps=channel.send) as send:
            await self.bot.tickets.recover(self.guild)
        send.assert_not_called()
        self.assertEqual(len(channel.messages), 2)
        self.assertTrue(channel.permissions_for(self.gfx).view_channel)
        self.assertEqual((await self.bot.store.ticket(pending['id'], self.guild.id))['state'], 'open')

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
            self.assertEqual(len(form.children), 3)
            self.assertLessEqual(len(form.title), 45)
            self.assertTrue(all(len(item.label) <= 45 for item in form.children))
            labels.add(form.children[1].label)
            validate_commission(self.payload(field))
        self.assertEqual(len(labels), 8)
        for payload in ({'_field': 'unknown'}, {'_field': 'gfx'}, {**self.payload(), 'extra': 'x'}):
            with self.assertRaises(UserError):
                validate_commission(payload)
        self.assertTrue(CommissionActions(self.bot).is_persistent())

    async def test_workflow_private_account_quote_progress_and_deletion(self):
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
        self.assertNotIn(channel, self.guild.channels)
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
        interaction = SimpleNamespace(guild=self.guild, channel_id=channel.id, channel=channel, user=self.member,
                                      response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()))
        with self.assertRaises(UserError):
            await view.close_ticket.callback(interaction)
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
        self.assertNotIn('ddh:commission:v1', {i.custom_id for i in support.messages[0].view.children})
        hub = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:commission_panel'))
        self.assertIn('ddh:commission:v1', {i.custom_id for i in hub.messages[0].view.children})
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
