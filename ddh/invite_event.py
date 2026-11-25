"""One four-week DDH campaign. Store observations and scores in one SQLite value."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from ddh.blueprint import commission_hub_overwrites
from ddh.content import embed
from ddh.notices import KST
from ddh.reviews import send_once
from ddh.server import UserError, channel_for, require_guild, roles_for
from ddh.views import GuardedView, reply

log = logging.getLogger(__name__)
KEY = 'event:invite:2026-11-22'
LAUNCH_DAY = datetime(2026, 11, 22, tzinfo=KST).date()
CAMPAIGN_GUILD = 1570276882669240383
DURATION = timedelta(weeks=4)
REWARDS = (5, 4, 3)
RECONCILE_SECONDS = 120


def standings(state):
    ordered = sorted(state['scores'].items(), key=lambda pair: (
        -pair[1], state['reached'][pair[0]], int(pair[0])))
    return [(rank, user, score) for rank, (user, score) in enumerate(ordered, 1)]


def tally(state, members, uses, now, *, retry=False):
    """Count interval totals only when every possible attribution yields the same score.

    members contains ID -> [most recent join timestamp, bot]. uses contains
    code -> [use count, inviter ID or None]. An entire interval commits atomically.
    """
    before = state['members']
    # Gateway events retain short visits between REST samples. Match the
    # join timestamp as well as the ID to distinguish replay from re-entry.
    arrivals = {tuple(row) for row in state.get('pending', [])}
    arrivals.update((uid, *record) for uid, record in members.items() if before.get(uid) != record)
    joined = {uid for uid, _, _ in arrivals}
    seen = set(state['seen'])
    eligible = {uid for uid, stamp, bot in arrivals if uid not in seen and not bot
                and state['start'] <= stamp < state['end']}
    old = state['uses']
    changes = {}
    for code, (count, owner) in uses.items():
        delta = count - old.get(code, [0])[0]
        if delta > 0:
            changes[owner] = changes.get(owner, 0) + delta
    state['last_checked'] = now
    state['last_observation'] = dict(arrivals=len(arrivals), eligible=len(eligible),
                                    invite_uses=sum(changes.values()))
    if retry and sum(changes.values()) != len(arrivals):
        # Discord's member and invite endpoints can become consistent at
        # different times. Keep BOTH baselines until a later observation.
        since = state.setdefault('reconcile_since', now)
        if now - since < RECONCILE_SECONDS:
            state['pending'] = [list(row) for row in sorted(arrivals)]
            state['tracking_status'] = 'retrying'
            return
    state.pop('reconcile_since', None)
    credited = {}
    # With a single source all eligible arrivals belong to it. With mixed
    # sources we can award aggregate scores only if every arrival qualifies.
    if sum(changes.values()) == len(arrivals) and arrivals:
        if len(changes) == 1:
            credited[next(iter(changes))] = len(eligible)
        elif len(eligible) == len(arrivals):
            credited = changes
    for owner, count in credited.items():
        if owner and count and owner not in joined:
            state['scores'][owner] = state['scores'].get(owner, 0) + count
            state['reached'][owner] = now
    awarded = sum(count for owner, count in credited.items() if owner and owner not in joined)
    unresolved = len(eligible) - awarded
    state['tracking_status'] = 'review_needed' if unresolved else 'ok'
    if arrivals or changes:
        record = dict(at=now, arrivals=[list(row) for row in sorted(arrivals)],
                      eligible=sorted(eligible), changes=changes, awarded=awarded,
                      unresolved=unresolved)
        state['recent_observations'] = (state.get('recent_observations', []) + [record])[-50:]
        if unresolved:
            state['unresolved_total'] = state.get('unresolved_total', 0) + unresolved
            log.warning('DDH invite event needs review: eligible=%s awarded=%s invite_uses=%s',
                        len(eligible), awarded, sum(changes.values()))
        else:
            log.info('DDH invite event counted: awarded=%s excluded=%s', awarded, len(joined) - awarded)
    population = sum(not bot and stamp < state['end'] for stamp, bot in members.values())
    # Require confirmed event growth as well as an observed 150 human members.
    if state['initial_population'] < 150 <= population and state['scores']:
        state['bonus'] = True
    state.update(members=members, seen=sorted(seen.union(members, joined)), uses=uses, pending=[])


def event_card(state, *, final=False, board=False):
    end = int(state['end'])
    card = embed('🏆 DDH 초대 이벤트 · 최종 결과' if final else
                 ('🏆 초대 순위표' if board else '🎉 DDH 4주 초대 이벤트'),
                 '집계가 마감되었습니다.' if final else
                 f"**마감 <t:{end}:F>** · <t:{end}:R>\n이벤트 채널에서 **내 초대 링크**를 눌러 친구에게 공유하세요.")
    bonus = int(state['bonus'])
    card.add_field(name='순위별 GFX 제작권', value=f'🥇 {5 + bonus}개　🥈 {4 + bonus}개　🥉 {3 + bonus}개', inline=False)
    card.add_field(name='150명 달성 보너스' + (' · 달성 🎉' if state['bonus'] else ''),
                   value='이벤트 초대로 서버가 150명에 도달하면 입상자마다 1개 추가!', inline=False)
    if final and state.get('partial_final'):
        card.description += '\n마지막 조회가 지연되어 확인된 초대까지만 반영했습니다.'
    if board or final:
        card.description += f"\n집계된 초대 **{sum(state['scores'].values())}명**"
        lines = []
        rows = standings(state)
        for rank, user, score in rows[:15]:
            prize = REWARDS[rank - 1] + int(state['bonus']) if rank <= 3 else 0
            reward = f' · GFX {prize}개' if final and prize else ''
            lines.append(f'**{rank}위** <@{user}> · {score}명{reward}')
        card.add_field(name='초대 순위 · 상위 15명', value='\n'.join(lines) or '현재 확정된 초대는 0명입니다. 아래에서 최근 조회 상태를 확인하세요.', inline=False)
        if state.get('last_checked'):
            status = '가입·초대 기록 재확인 중' if state.get('tracking_status') == 'retrying' else '조회 완료'
            card.add_field(name='집계 확인', value=f"{status} · <t:{int(state['last_checked'])}:R>", inline=False)
        if state.get('unresolved_total'):
            card.add_field(name='운영자 확인 필요',
                           value=f"초대 경로를 확정하지 못한 신규 가입 {state['unresolved_total']}명 · 점수 미반영", inline=False)
    card.add_field(name='집계 기준', value='초대자가 확인된 신규 가입만 1인 1회 집계합니다. 봇·재가입은 제외하며 동점은 해당 점수에 먼저 도달한 순, 같은 집계 시각에는 Discord ID 오름차순입니다.', inline=False)
    return card


class InviteEventView(GuardedView):
    @discord.ui.button(label='내 초대 링크', style=discord.ButtonStyle.primary, custom_id='ddh:invite:20261122')
    async def personal_link(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        url = await self.bot.invite_event.personal_link(interaction.guild, interaction.user, interaction.channel_id)
        state = await self.bot.store.get(interaction.guild.id, KEY)
        score = state['scores'].get(str(interaction.user.id), 0)
        await reply(interaction, f'내 확정 초대: **{score}명** · 내 계정의 링크는 모두 합산돼요.\n{url}')


class InviteEvent:
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def save(self, guild, state):
        await self.bot.store.set(guild.id, KEY, state)

    async def record_join(self, member):
        guild = member.guild
        if guild.id != CAMPAIGN_GUILD or guild.id != self.bot.settings.guild_id or not member.joined_at:
            return
        async with self.lock:
            state = await self.bot.store.get(guild.id, KEY)
            stamp = member.joined_at.timestamp()
            if not state or state.get('frozen') or not state['start'] <= stamp < state['end']:
                return
            uid = str(member.id)
            if state['members'].get(uid) == [stamp, member.bot]:
                return
            row = [uid, stamp, member.bot]
            pending = state.setdefault('pending', [])
            if row not in pending:
                pending.append(row)
                await self.save(guild, state)

    async def personal_link(self, guild, actor, channel_id):
        require_guild(guild, self.bot.settings)
        async with self.lock:
            state = await self.bot.store.get(guild.id, KEY)
            if not state or channel_id != state['channel'] or datetime.now(timezone.utc).timestamp() >= state['end']:
                raise UserError('진행 중인 초대 이벤트 채널에서 이용해 주세요.')
            actor = await guild.fetch_member(actor.id)
            role = await self.bot.store.get(guild.id, 'role:member')
            if actor.bot or actor.pending or actor.is_timed_out() or not any(r.id == role for r in actor.roles):
                raise UserError('DDH 멤버 인증 후 참여해 주세요.')
            uid = str(actor.id)
            for inv in await guild.invites():
                if state['links'].get(inv.code) == uid:
                    return inv.url
            entry = await channel_for(guild, self.bot.store, 'verification')
            inv = await entry.create_invite(max_age=0, max_uses=0, unique=True, reason='DDH 이벤트 개인 초대 링크')
            state['links'][inv.code] = uid
            await self.save(guild, state)
            return inv.url

    async def resources(self, guild):
        store = self.bot.store
        roles = await roles_for(guild, store)
        policy = commission_hub_overwrites(guild, roles)
        async def get_or_create(key, name, kind, parent=None):
            saved = await store.get(guild.id, KEY + key)
            if saved:
                return await guild.fetch_channel(saved)
            # Look up the exact event resource after a create-before-save crash.
            channels = guild.channels
            matches = [c for c in channels if isinstance(c, kind) and c.name == name
                       and (not parent or c.category_id == parent.id)]
            if len(matches) > 1:
                raise UserError('같은 이름의 이벤트 채널이 둘 이상입니다.')
            if matches:
                channel = matches[0]
            elif parent:
                channel = await guild.create_text_channel(name, category=parent, overwrites=policy,
                                                          reason='DDH 초대 이벤트')
            else:
                channel = await guild.create_category(name, overwrites=policy, reason='DDH 초대 이벤트')
            await store.set(guild.id, KEY + key, channel.id)
            return channel
        category = await get_or_create(':category', '🎉 초대 이벤트', discord.CategoryChannel)
        return await get_or_create(':board', '초대순위-ranking', discord.TextChannel, category)

    async def snapshot(self, guild, links):
        def counters(invites):
            return {i.code: [i.uses, links.get(i.code) or
                             (str(i.inviter.id) if i.inviter and not i.inviter.bot else None)]
                    for i in invites if i.uses is not None}
        first = counters(await guild.invites())
        members = {str(m.id): [m.joined_at.timestamp(), m.bot]
                   async for m in guild.fetch_members(limit=None) if m.joined_at}
        if first != counters(await guild.invites()):
            return None  # Retry from the previous durable observation.
        return members, first

    async def tick(self, guild, now=None):
        if guild.id != CAMPAIGN_GUILD or guild.id != self.bot.settings.guild_id:
            return
        async with self.lock:
            now = now or datetime.now(timezone.utc)
            day = now.astimezone(KST).date()
            state = await self.bot.store.get(guild.id, KEY)
            if day < LAUNCH_DAY or (not state and day >= LAUNCH_DAY + DURATION):
                return
            if state and state.get('announced_end'):
                return
            if not state:
                entry = await channel_for(guild, self.bot.store, 'verification')
                if not entry.permissions_for(guild.me).create_instant_invite:
                    raise UserError('인증 채널에서 Dialian의 초대 만들기 권한을 확인해 주세요.')
                # Validate tracking access before announcing a live competition.
                sample = await self.snapshot(guild, {})
                if sample is None:
                    return
                channel = await self.resources(guild)
                members, uses = sample
                state = dict(start=now.timestamp(), end=(now + DURATION).timestamp(), channel=channel.id,
                             members=members, seen=sorted(members), uses=uses, links={}, scores={}, reached={}, bonus=False,
                             initial_population=sum(not row[1] for row in members.values()))
                await self.save(guild, state)
            ended = now.timestamp() >= state['end']
            if not state.get('frozen'):
                try:
                    sample = await self.snapshot(guild, state['links'])
                except (discord.HTTPException, discord.ClientException):
                    if not ended:
                        raise
                    sample = None
                if sample is None:
                    if not ended or now.timestamp() < state['end'] + 120:
                        return
                    # Stop awarding at the deadline even during a persistent
                    # invite API outage, and publish the last confirmed totals.
                    state['partial_final'] = True
                else:
                    tally(state, *sample, now.timestamp(),
                          retry=now.timestamp() < state['end'] + RECONCILE_SECONDS)
                    if state.get('tracking_status') == 'retrying' and now.timestamp() < state['end'] + 120:
                        await self.save(guild, state)
                        await self.publish(guild, state)
                        return
                state['frozen'] = ended
                await self.save(guild, state)
            await self.publish(guild, state)

    async def publish(self, guild, state):
        bot = self.bot
        final = bool(state.get('frozen'))
        channel = guild.get_channel(state['channel']) or await guild.fetch_channel(state['channel'])
        marker = 'invite-board:20261122'
        after = datetime.fromtimestamp(state['start'], timezone.utc).isoformat()
        card = event_card(state, final=final, board=True)
        card.set_footer(text=f'DDH:{marker}:v1')
        # Refresh periodically even at zero, and repair an old/stale board
        # once on upgrade without changing the campaign dates or points.
        signature = [state['scores'], state['bonus'], final, 2,
                     int(state.get('last_checked', 0)) // 300,
                     state.get('tracking_status'), state.get('unresolved_total', 0)]
        if state.get('display') != signature:
            message = None
            if state.get('message'):
                try:
                    message = await channel.fetch_message(state['message'])
                except discord.NotFound:
                    pass
            view = None if final else InviteEventView(bot)
            if message:
                await message.edit(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await send_once(bot, channel, marker, card, after, view=view)
            state.update(message=message.id, display=signature)
            await self.save(guild, state)
        phase = 'end' if final else 'start'
        if not state.get('announced_' + phase):
            news = await channel_for(guild, bot.store, 'announcements')
            card = event_card(state, final=final)
            card.add_field(name='이벤트 순위표', value=channel.mention, inline=False)
            await send_once(bot, news, f'invite-{phase}:20261122', card, after)
            log.info('DDH invite event announcement delivered: %s', phase)
            state['announced_' + phase] = True
            await self.save(guild, state)
