import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from config import Settings
from ddh.bot import DDHCommands, Dialian
from ddh.blueprint import FIELDS, access
from ddh.commissions import assign, form_labels, progress, quote, remove_account, save_account, send_account
from ddh.commission_views import AccountModal
from ddh.reviews import configure_reviews, eligible_review, recover_reviews, submit_review
from ddh.review_views import ReviewModal, ReviewRequestView
from ddh.server import UserError, provision, roles_for
from ddh.store import Store
from fakes import Guild


class AccountReviewTests(unittest.IsolatedAsyncioTestCase):
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
        await self.bot.store.set(self.guild.id, 'release:1.4.0:enabled', False)
        await provision(self.guild, self.bot.store, self.bot.settings)
        await self.bot.store.set(self.guild.id, 'setup_complete', True)
        self.roles = await roles_for(self.guild, self.bot.store)
        self.customer = self.guild.member(21, [self.roles['member']])
        self.staff = self.guild.member(22, [self.roles['staff']])
        self.dev = self.guild.member(23, [self.roles['member'], self.roles['field:gfx']])
        self.other = self.guild.member(24, [self.roles['member'], self.roles['field:gfx']])
        self.owner = self.guild.member(self.guild.owner_id)
        self.category = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'category:commissions'))

    async def ticket(self, *, field='gfx'):
        payload = {'_field': field, '_developer': self.dev.id,
                   **{label: 'private brief never publish' for label in form_labels(field)}}
        return await self.bot.tickets.create(self.guild, self.customer, 'commission', payload)

    async def delivered(self):
        channel = await self.ticket()
        await progress(self.bot, self.guild, channel, self.dev, 'delivered')
        return channel

    def snapshot(self):
        return ({c.id: (c.name, c.category_id, c.overwrites) for c in self.guild.channels},
                {r.id: (r.name, r.position, r.permissions.value) for r in self.guild.roles})

    async def test_staff_register_send_and_delete_the_assigned_developers_account(self):
        channel = await self.ticket()
        before = self.snapshot()
        await save_account(self.bot, self.guild, self.staff, 'Developer Bank', '123-456-789', 'Developer', developer_id=self.dev.id)
        await save_account(self.bot, self.guild, self.other, 'Other Bank', '999-999-999', 'Other')
        await quote(self.bot, self.guild, channel, self.dev, '10000', '기존 견적 조건')
        await send_account(self.bot, self.guild, channel, self.staff)
        await send_account(self.bot, self.guild, channel, self.dev)
        cards = [m for m in channel.messages if '담당 개발자 입금 계좌' == m.embeds[0].title.split(' · ')[-1]]
        self.assertEqual(len(cards), 1)
        self.assertIn('123-456-789', cards[0].embeds[0].description)
        self.assertNotIn('999-999-999', cards[0].embeds[0].description)
        self.assertIsNone(await self.bot.store.account(self.guild.id, self.staff.id))
        self.assertIn('기존 견적 조건', str([m.embeds[0].to_dict() for m in channel.messages]))
        ticket = await self.bot.store.channel_ticket(self.guild.id, channel.id)
        self.assertEqual(ticket['stage'], 'quoted')  # sending an account never changes the workflow
        self.assertEqual(self.snapshot(), before)
        async with self.bot.store.connect() as db:
            logs = str([row[0] for row in await (await db.execute('SELECT body FROM operations_log')).fetchall()])
        self.assertNotIn('123-456-789', logs)
        await remove_account(self.bot, self.guild, self.staff, developer_id=self.dev.id)
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, channel, self.dev)

    async def test_proxy_account_authorization_rechecked_at_modal_submission(self):
        for actor in (self.customer, self.other):
            with self.assertRaises(UserError):
                await save_account(self.bot, self.guild, actor, 'Bank', '123456', 'Name', developer_id=self.dev.id)
            with self.assertRaises(UserError):
                await remove_account(self.bot, self.guild, actor, developer_id=self.dev.id)
        modal = AccountModal(self.bot, self.dev.id)
        for item, value in zip(modal.children, ('Bank', '123456', 'Name')):
            item._value = value
        await self.staff.remove_roles(self.roles['staff'])
        interaction = SimpleNamespace(guild=self.guild, user=self.staff, response=SimpleNamespace(defer=AsyncMock()))
        with self.assertRaises(UserError):
            await modal.on_submit(interaction)
        self.assertIsNone(await self.bot.store.account(self.guild.id, self.dev.id))
        await save_account(self.bot, self.guild, self.owner, 'Bank', '123456', 'Name', developer_id=self.dev.id)
        await self.dev.remove_roles(self.roles['field:gfx'])
        await remove_account(self.bot, self.guild, self.dev)
        self.assertIsNone(await self.bot.store.account(self.guild.id, self.dev.id))

    async def test_send_rejects_public_channels_nonmanagers_and_closed_tickets(self):
        channel = await self.ticket()
        await save_account(self.bot, self.guild, self.dev, 'Bank', '123456', 'Name')
        for actor in (self.customer, self.other):
            with self.assertRaises(UserError):
                await send_account(self.bot, self.guild, channel, actor)
        public = self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:general'))
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, public, self.staff)
        original = channel.overwrites
        await channel.set_permissions(self.other, overwrite=access())
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, channel, self.staff)
        await channel.edit(overwrites=original)
        await self.bot.tickets.close(self.guild, channel, self.dev, '종료')
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, channel, self.staff)

    async def test_reassignment_retires_old_account_without_leaking_new_account(self):
        channel = await self.ticket()
        await save_account(self.bot, self.guild, self.dev, 'Bank', '123456', 'Name')
        await send_account(self.bot, self.guild, channel, self.staff)
        await assign(self.bot, self.guild, channel, self.staff, self.other)
        self.assertNotIn('123456', str([m.embeds[0].to_dict() for m in channel.messages]))
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, channel, self.dev)
        with self.assertRaises(UserError):
            await send_account(self.bot, self.guild, channel, self.staff)

    async def test_reviews_setup_changes_only_new_channel_and_exactly_syncs_existing_category(self):
        custom = self.guild.make_role('소유자 커스텀 역할')
        await self.category.edit(overwrites={**self.category.overwrites,
            custom: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=False)})
        await self.ticket()
        before = self.snapshot()
        reviews = await configure_reviews(self.bot, self.guild)
        self.assertEqual(reviews.overwrites, self.category.overwrites)
        self.assertTrue(reviews.permissions_synced)
        self.assertEqual(len(self.guild.channels), len(before[0]) + 1)
        self.assertEqual({key: value for key, value in self.snapshot()[0].items() if key != reviews.id}, before[0])
        self.assertEqual(self.snapshot()[1], before[1])
        reviews.name = '운영자가-꾸민-후기'
        same = await configure_reviews(self.bot, self.guild)
        self.assertEqual(same.id, reviews.id)
        self.assertEqual(same.name, '운영자가-꾸민-후기')
        self.assertEqual(reviews.messages, [])  # setup is not an announcement
        interaction = SimpleNamespace(guild=self.guild, user=self.staff)
        with self.assertRaises(UserError):
            await DDHCommands(self.bot).reviews.callback(DDHCommands(self.bot), interaction)

    async def test_category_can_be_selected_without_reconfiguring_commission_hub(self):
        alternate = await self.guild.create_category('직접 만든 커미션', overwrites=self.category.overwrites)
        before = self.snapshot()
        reviews = await configure_reviews(self.bot, self.guild, category=alternate)
        self.assertEqual(reviews.category_id, alternate.id)
        self.assertEqual(await self.bot.store.get(self.guild.id, 'category:commissions'), self.category.id)
        self.assertEqual({key: value for key, value in self.snapshot()[0].items() if key != reviews.id}, before[0])

    async def test_completed_customer_review_survives_closed_readonly_ticket(self):
        reviews = await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        prompt = next(m for m in channel.messages if isinstance(m.view, ReviewRequestView))
        self.assertTrue(prompt.view.is_persistent())
        await self.bot.tickets.close(self.guild, channel, self.dev, '완료')
        self.assertFalse(channel.permissions_for(self.customer).send_messages)
        result = await submit_review(self.bot, self.guild, channel, self.customer, 4, '정성스러운 작업이었습니다.')
        self.assertTrue(result['published_at'])
        card = reviews.messages[0].embeds[0]
        self.assertIn('4/5', card.title)
        self.assertIn('GFX', card.description)
        self.assertIn(f'<@{self.dev.id}>', card.description)
        self.assertNotIn('private brief', str(card.to_dict()))
        self.assertEqual(len([m for m in channel.messages if isinstance(m.view, ReviewRequestView)]), 1)
        self.assertEqual((await self.bot.store.channel_ticket(self.guild.id, channel.id))['state'], 'closed')

    async def test_receipt_recovers_without_duplicate_public_post_or_developer_ping(self):
        from ddh.review_views import ReviewReceiptView
        reviews = await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        with patch.object(self.bot.store, 'mark_review_receipt', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                await submit_review(self.bot, self.guild, channel, self.customer, 5, '좋았습니다')
        await recover_reviews(self.bot, self.guild)
        await recover_reviews(self.bot, self.guild)
        self.assertEqual(len(reviews.messages), 1)
        receipts = [m for m in channel.messages if isinstance(m.view, ReviewReceiptView)]
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertIn(f"<@{self.dev.id}>", receipt.content)
        self.assertIn('5/5점', receipt.embeds[0].description)
        self.assertIn(f"<@{self.customer.id}>", reviews.messages[0].embeds[0].description)
        self.assertIn(str(reviews.messages[0].id), receipt.view.children[-1].url)
        self.assertFalse(receipt.view.close_ticket.disabled)

    async def test_command_close_archives_only_after_authorized_confirmation(self):
        cmds = DDHCommands(self.bot)
        channel = await self.ticket()
        self.assertEqual(channel.name, 'gfx-0001')
        interaction = SimpleNamespace(guild=self.guild, channel=channel, channel_id=channel.id,
            response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()))
        for actor in (self.customer, self.other):
            interaction.user = actor
            with self.assertRaises(UserError):
                await cmds.close_commission.callback(cmds, interaction)
        interaction.user = self.dev
        await cmds.close_commission.callback(cmds, interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertEqual((await self.bot.tickets.current(self.guild, channel.id))['state'], 'open')
        modal.reason._value = '작업 완료 확인'
        await modal.on_submit(interaction)
        self.assertEqual(channel.name, '종료-gfx-0001')
        self.assertFalse(channel.permissions_for(self.customer).send_messages)
        self.assertTrue(channel.permissions_for(self.customer).view_channel)
        self.assertFalse(channel.permissions_for(self.other).view_channel)

    async def test_delivery_and_resend_commands_respect_assignment(self):
        await configure_reviews(self.bot, self.guild)
        channel = await self.ticket()
        cmds = DDHCommands(self.bot)
        interaction = SimpleNamespace(user=self.dev, guild=self.guild, channel=channel, channel_id=channel.id,
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True), followup=SimpleNamespace(send=AsyncMock()))
        with self.assertRaises(UserError):
            await cmds.review_request.callback(cmds, interaction)
        await cmds.complete.callback(cmds, interaction)
        interaction.user = self.other
        with self.assertRaises(UserError):
            await cmds.review_request.callback(cmds, interaction)
        interaction.user = self.staff
        await cmds.review_request.callback(cmds, interaction)
        with self.assertRaises(UserError):
            await cmds.review_request.callback(cmds, interaction)
        prompts = [m for m in channel.messages if isinstance(m.view, ReviewRequestView)]
        self.assertEqual(len(prompts), 2)
        self.assertIs(channel.messages[-1], prompts[-1])

    async def test_existing_default_name_migrates_once_then_preserves_owner_edits(self):
        from ddh.readability import COPY_KEY, apply_requested_copy
        await self.bot.store.set(self.guild.id, COPY_KEY, False)
        channel = await self.ticket()
        await channel.edit(name='commission-0001')
        before = channel.overwrites
        self.assertTrue(await apply_requested_copy(self.bot, self.guild))
        self.assertEqual(channel.name, 'gfx-0001')
        self.assertEqual(channel.overwrites, before)
        await channel.edit(name='custom')
        self.assertTrue(await apply_requested_copy(self.bot, self.guild))
        self.assertEqual(channel.name, 'custom')

    async def test_rejects_early_or_foreign_reviews_and_cancelled_tickets(self):
        await configure_reviews(self.bot, self.guild)
        channel = await self.ticket()
        with self.assertRaises(UserError):
            await submit_review(self.bot, self.guild, channel, self.customer, 5, '아직 제작 전')
        await progress(self.bot, self.guild, channel, self.dev, 'delivered')
        for actor in (self.dev, self.staff, self.other):
            with self.assertRaises(UserError):
                await submit_review(self.bot, self.guild, channel, actor, 5, '대리 후기')
        for rating in (0, 6, True, '5'):
            with self.assertRaises(UserError):
                await submit_review(self.bot, self.guild, channel, self.customer, rating, '')
        await progress(self.bot, self.guild, channel, self.dev, 'working')
        await self.bot.tickets.close(self.guild, channel, self.dev, '제작 중 취소')
        with self.assertRaises(UserError):
            await eligible_review(self.bot, self.guild, channel, self.customer)

    async def test_duplicate_racing_submissions_keep_first_review_and_no_mentions(self):
        reviews = await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        with patch.object(reviews, 'send', wraps=reviews.send) as send:
            await asyncio.gather(*[submit_review(self.bot, self.guild, channel, self.customer, 3, '@everyone 잘 받았습니다.') for _ in range(3)])
        self.assertEqual(len(reviews.messages), 1)
        self.assertEqual(send.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.assertNotIn('@everyone', reviews.messages[0].embeds[0].description)
        again = await submit_review(self.bot, self.guild, channel, self.customer, 5, '변경 불가')
        self.assertEqual(again['rating'], 3)

    async def test_delivery_failure_retries_after_restart_without_touching_permissions(self):
        reviews = await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        failure = discord.HTTPException(SimpleNamespace(status=503, reason='Unavailable'), 'retry')
        with patch.object(reviews, 'send', side_effect=failure):
            result = await submit_review(self.bot, self.guild, channel, self.customer, 5, '후기')
        self.assertIsNone(result['published_at'])
        restarted = Dialian(self.bot.settings)
        self.addAsyncCleanup(restarted.close)
        await restarted.store.initialize()
        reviews.name = '수동 이름'
        await reviews.set_permissions(self.other, overwrite=access())
        before = self.snapshot()
        await recover_reviews(restarted, self.guild)
        await recover_reviews(restarted, self.guild)
        with patch.object(restarted, 'get_guild', return_value=self.guild):
            await restarted.on_ready()
        self.assertEqual(len(reviews.messages), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.guild.get_channel(await self.bot.store.get(self.guild.id, 'channel:updates')).messages, [])

    async def test_send_before_database_crash_is_recovered_beyond_last_100_posts(self):
        reviews = await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        with patch.object(self.bot.store, 'mark_review', side_effect=RuntimeError('DB write failed')):
            with self.assertRaises(RuntimeError):
                await submit_review(self.bot, self.guild, channel, self.customer, 2, '보완 필요')
        for _ in range(105):
            await reviews.send('다른 메시지')
        await recover_reviews(self.bot, self.guild)
        self.assertEqual(len([m for m in reviews.messages if m.embeds]), 1)

    async def test_missing_review_channel_does_not_block_delivery_or_close_and_prompt_recovers(self):
        channel = await self.delivered()
        await self.bot.tickets.close(self.guild, channel, self.dev, '완료')
        self.assertEqual((await self.bot.store.channel_ticket(self.guild.id, channel.id))['state'], 'closed')
        await configure_reviews(self.bot, self.guild)
        await recover_reviews(self.bot, self.guild)
        self.assertEqual(len([m for m in channel.messages if isinstance(m.view, ReviewRequestView)]), 1)

    async def test_stale_review_modal_and_revoked_membership_are_rejected(self):
        await configure_reviews(self.bot, self.guild)
        channel = await self.delivered()
        await assign(self.bot, self.guild, channel, self.staff, self.other)
        await progress(self.bot, self.guild, channel, self.other, 'delivered')
        modal = ReviewModal(self.bot, self.customer.id, channel.id, self.dev.id, 5)
        interaction = SimpleNamespace(guild=self.guild, channel=channel, channel_id=channel.id,
                                      user=self.customer, response=SimpleNamespace(defer=AsyncMock()))
        with self.assertRaisesRegex(UserError, '담당자가 변경'):
            await modal.on_submit(interaction)
        await self.customer.remove_roles(self.roles['member'])
        with self.assertRaises(UserError):
            await submit_review(self.bot, self.guild, channel, self.customer, 5, '')

    async def test_every_field_supports_review_and_old_accounts_survive_migration(self):
        await save_account(self.bot, self.guild, self.dev, 'Bank', '123456', 'Name')
        async with self.bot.store.connect() as db:
            await db.execute('DROP TABLE commission_reviews')
            await db.commit()
        await Store(self.bot.store.path).initialize()
        self.assertEqual((await self.bot.store.account(self.guild.id, self.dev.id))['account'], '123456')
        reviews = await configure_reviews(self.bot, self.guild)
        for index, field in enumerate(FIELDS):
            self.customer = self.guild.member(100 + index, [self.roles['member']])
            await self.dev.add_roles(self.roles[f'field:{field}'])
            channel = await self.ticket(field=field)
            await progress(self.bot, self.guild, channel, self.dev, 'delivered')
            await submit_review(self.bot, self.guild, channel, self.customer, index % 5 + 1, '')
        self.assertEqual(len(reviews.messages), len(FIELDS))
