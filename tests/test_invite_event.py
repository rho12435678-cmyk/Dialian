import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import Settings
from ddh.bot import Dialian
from ddh.invite_event import CAMPAIGN_GUILD, KEY, InviteEvent, event_card, standings, tally
from ddh.notices import KST, publish_due
from ddh.server import provision
from fakes import Guild


class InviteScoreTests(unittest.TestCase):
    def state(self):
        return dict(start=10, end=1000, seen=['8'], members={'8': [5, False]}, uses={},
                    scores={}, reached={}, bonus=False, initial_population=1)

    def test_mixed_sources_are_safe_only_if_all_new_members_qualify(self):
        s = self.state()
        members = {'8': [5, False], '9': [15, False], '10': [16, False]}
        uses = {'a': [1, '1'], 'b': [1, '2']}
        tally(s, members, uses, 20)
        self.assertEqual(standings(s), [(1, '1', 1), (2, '2', 1)])
        tally(s, members, uses, 23)
        self.assertEqual(s['scores'], {'1': 1, '2': 1})
        members = dict(members, **{'9': [28, False], '11': [29, False]})
        tally(s, members, {'a': [2, '1'], 'b': [2, '2']}, 30)
        self.assertEqual(s['scores'], {'1': 1, '2': 1})

    def test_bots_rejoins_and_joins_after_cutoff_never_score(self):
        s = self.state()
        members = {'8': [12, False], '9': [13, True], '10': [14, False], '11': [1001, False]}
        tally(s, members, {'link': [4, '1']}, 1002)
        self.assertEqual(s['scores'], {'1': 1})

    def test_short_visit_is_not_credited_again_on_return(self):
        s = self.state()
        s['pending'] = [['12', 12, False]]
        tally(s, s['members'], {'link': [1, '1']}, 13)
        tally(s, {'12': [18, False]}, {'link': [2, '1']}, 19)
        self.assertEqual(s['scores'], {'1': 1})

    def test_threshold_and_prizes_are_latched(self):
        s = self.state()
        roster = {str(i): [5, False] for i in range(100, 249)}
        s.update(seen=list(roster), members=roster, initial_population=149)
        tally(s, {**roster, '999': [12, False]}, {'one': [1, '1']}, 13)
        self.assertTrue(s['bonus'])
        tally(s, {}, {}, 14)
        self.assertIn('GFX 6개', str(event_card(s, final=True).to_dict()))

    def test_delayed_invite_counter_preserves_three_arrivals_across_restart(self):
        import json
        s = self.state()
        members = {**s['members'], **{str(i): [15, False] for i in (9, 10, 11)}}
        tally(s, members, {}, 20, retry=True)
        self.assertEqual(s['tracking_status'], 'retrying')
        self.assertEqual(s['seen'], ['8'])
        # Persist/reload exactly as production does; no transient state needed.
        s = json.loads(json.dumps(s))
        tally(s, members, {'link': [3, '1']}, 80, retry=True)
        self.assertEqual(s['scores'], {'1': 3})
        self.assertEqual(s['tracking_status'], 'ok')
        tally(s, members, {'link': [3, '1']}, 140, retry=True)
        self.assertEqual(s['scores'], {'1': 3})

    def test_counter_arriving_before_member_list_is_retried(self):
        s = self.state()
        tally(s, s['members'], {'link': [1, '1']}, 20, retry=True)
        self.assertEqual(s['uses'], {})
        tally(s, {**s['members'], '9': [15, False]}, {'link': [1, '1']}, 80, retry=True)
        self.assertEqual(s['scores'], {'1': 1})

    def test_unresolved_window_is_logged_and_does_not_block_later_joins(self):
        s = self.state()
        members = {**s['members'], '9': [15, False]}
        tally(s, members, {}, 20, retry=True)
        with self.assertLogs('ddh.invite_event', level='WARNING'):
            tally(s, members, {}, 140, retry=True)
        self.assertEqual(s['unresolved_total'], 1)
        self.assertEqual(s['scores'], {})
        self.assertEqual(s['recent_observations'][0]['eligible'], ['9'])
        tally(s, {**members, '10': [145, False]}, {'link': [1, '2']}, 150, retry=True)
        self.assertEqual(s['scores'], {'2': 1})
        self.assertEqual(s['unresolved_total'], 1)

    def test_bot_or_rejoin_in_mixed_sources_is_never_guessed(self):
        s = self.state()
        tally(s, {'8': [15, False], '9': [16, False]},
              {'one': [1, '1'], 'two': [1, '2']}, 20, retry=True)
        self.assertEqual(s['scores'], {})
        self.assertEqual(s['unresolved_total'], 1)


class CampaignTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.guild = Guild(CAMPAIGN_GUILD)
        self.bot = Dialian(Settings('test', self.guild.id, Path(tmp.name) / 'db'))
        self.addAsyncCleanup(self.bot.close)
        await self.bot.store.initialize()
        await provision(self.guild, self.bot.store, self.bot.settings)
        await self.bot.store.set(self.guild.id, 'setup_complete', True)
        self.when = datetime(2026, 11, 22, 13, tzinfo=KST)
        self.guild.invites = AsyncMock(return_value=[])
        self.guild.me.bot = True
        self.bot_member = self.guild.me
        self.bot_member.joined_at = self.when - timedelta(days=1)
        self.verification = await self.guild.create_text_channel('인증')
        await self.bot.store.set(self.guild.id, 'channel:verification', self.verification.id)

    async def test_lifecycle_restart_countdown_bonus_and_final_publication(self):
        event, guild = self.bot.invite_event, self.guild
        t = self.when
        await event.tick(guild, t)
        state = await self.bot.store.get(guild.id, KEY)
        self.assertEqual(state['end'] - state['start'], 28 * 86400)
        channel = guild.get_channel(state['channel'])
        news = guild.get_channel(await self.bot.store.get(guild.id, 'channel:announcements'))
        self.assertTrue(channel.messages[0].view.is_persistent())
        owner = guild.member(300)
        person = guild.member(301)
        owner.joined_at = t + timedelta(seconds=2)
        person.joined_at = t + timedelta(seconds=3)
        await event.tick(guild, t + timedelta(seconds=5))
        await event.tick(guild, t + timedelta(seconds=125))
        friend = guild.member(302)
        friend.joined_at = t + timedelta(seconds=135)
        invite = SimpleNamespace(code='abc', uses=1, inviter=owner)
        guild.invites.return_value = [invite]
        await event.record_join(friend)
        await InviteEvent(self.bot).tick(guild, t + timedelta(minutes=3))
        await event.tick(guild, t + timedelta(weeks=4))
        await event.tick(guild, t + timedelta(weeks=5))
        self.assertEqual(len(channel.messages), 1)
        self.assertIsNone(channel.messages[0].view)
        self.assertEqual(len(news.messages), 2)
        self.assertIn('GFX 5개', str(news.messages[-1].embeds[0].to_dict()))
        self.assertTrue((await self.bot.store.get(guild.id, KEY))['announced_end'])

    async def test_personal_link_is_private_and_reused(self):
        guild, event = self.guild, self.bot.invite_event
        await event.tick(guild, self.when)
        state = await self.bot.store.get(guild.id, KEY)
        role = guild.get_role(await self.bot.store.get(guild.id, 'role:member'))
        actor = guild.member(42, [role])
        invite = SimpleNamespace(code='abc', url='https://discord.gg/abc', uses=0, inviter=guild.me)
        with patch('ddh.invite_event.datetime') as clock, patch.object(self.verification, 'create_invite',
                new=AsyncMock(return_value=invite)) as create:
            clock.now.return_value = self.when
            self.assertEqual(await event.personal_link(guild, actor, state['channel']), invite.url)
            guild.invites.return_value = [invite]
            self.assertEqual(await event.personal_link(guild, actor, state['channel']), invite.url)
            create.assert_awaited_once()

    async def test_board_recovers_delayed_counts_without_extra_announcement(self):
        guild, event, t = self.guild, self.bot.invite_event, self.when
        await event.tick(guild, t)
        state = await self.bot.store.get(guild.id, KEY)
        board = guild.get_channel(state['channel'])
        for i in (401, 402, 403):
            guild.member(i).joined_at = t + timedelta(seconds=10)
        await event.tick(guild, t + timedelta(seconds=60))
        self.assertIn('재확인 중', str(board.messages[0].embeds[0].to_dict()))
        guild.invites.return_value = [SimpleNamespace(code='abc', uses=3, inviter=SimpleNamespace(id=1, bot=False))]
        await event.tick(guild, t + timedelta(seconds=120))
        state = await self.bot.store.get(guild.id, KEY)
        self.assertEqual(state['scores'], {'1': 3})
        self.assertIn('**3명**', board.messages[0].embeds[0].description)
        self.assertEqual(len(board.messages), 1)

    async def test_deadline_waits_for_delayed_counters_then_finishes(self):
        guild, event, t = self.guild, self.bot.invite_event, self.when
        await event.tick(guild, t)
        end = t + timedelta(weeks=4)
        guild.member(401).joined_at = end - timedelta(seconds=10)
        await event.tick(guild, end)
        s = await self.bot.store.get(guild.id, KEY)
        self.assertFalse(s.get('announced_end'))
        guild.invites.return_value = [SimpleNamespace(code='abc', uses=1,
                                                     inviter=SimpleNamespace(id=1, bot=False))]
        await event.tick(guild, end + timedelta(seconds=60))
        s = await self.bot.store.get(guild.id, KEY)
        self.assertTrue(s['announced_end'])
        self.assertEqual(s['scores'], {'1': 1})

    async def test_deadline_unresolved_counter_cannot_hold_event_open(self):
        guild, event, t = self.guild, self.bot.invite_event, self.when
        await event.tick(guild, t)
        end = t + timedelta(weeks=4)
        guild.member(401).joined_at = end - timedelta(seconds=10)
        await event.tick(guild, end)
        await event.tick(guild, end + timedelta(seconds=120))
        s = await self.bot.store.get(guild.id, KEY)
        self.assertTrue(s['announced_end'])
        self.assertEqual(s['unresolved_total'], 1)
        self.assertEqual(s['scores'], {})

    async def test_guide_evening_every_other_day_only_in_chat(self):
        g, b = self.guild, self.bot
        day = self.when.replace(hour=18)
        self.assertFalse(await publish_due(b, g, day - timedelta(seconds=1), chat=True))
        self.assertTrue(await publish_due(b, g, day, chat=True))
        self.assertFalse(await publish_due(b, g, day + timedelta(minutes=2), chat=True))
        self.assertFalse(await publish_due(b, g, day + timedelta(days=1), chat=True))
        self.assertTrue(await publish_due(b, g, day + timedelta(days=2), chat=True))
        chat = g.get_channel(await b.store.get(g.id, 'channel:general'))
        self.assertEqual(len(chat.messages), 2)
        self.assertFalse(chat.messages[0].content)
