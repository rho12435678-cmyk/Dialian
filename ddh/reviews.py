"""Customer-authored reviews; only the owner command creates/syncs their channel."""
import logging
from datetime import datetime, timezone

import discord

from ddh.blueprint import FIELDS
from ddh.clients import grant_client
from ddh.commissions import ensure_private, manager
from ddh.content import embed
from ddh.server import UserError, require_guild

log = logging.getLogger(__name__)
CHANNEL_KEY = 'channel:commission_reviews'


async def review_channel(bot, guild, channel_id=None):
    channel_id = channel_id or await bot.store.get(guild.id, CHANNEL_KEY)
    if not channel_id:
        raise UserError('후기 채널이 준비되지 않았습니다. 소유자가 /ddh reviews를 실행해 주세요.')
    try:
        channel = await guild.fetch_channel(channel_id)
    except discord.NotFound as error:
        raise UserError('후기 채널이 없습니다. 소유자가 /ddh reviews로 확인해 주세요.') from error
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise UserError('후기 채널 연결을 확인해 주세요.')
    permissions = channel.permissions_for(guild.me)
    if not all(getattr(permissions, key) for key in ('view_channel', 'send_messages', 'embed_links', 'read_message_history')):
        raise UserError('후기 채널에서 봇의 보기·전송·임베드·기록 읽기 권한이 필요합니다.')
    return channel


async def configure_reviews(bot, guild, *, category=None):
    """Caller holds mutation_lock and has verified the actual guild owner."""
    require_guild(guild, bot.settings)
    if category is None:
        category_id = await bot.store.get(guild.id, 'category:commissions')
    else:
        if category.guild.id != guild.id:
            raise UserError('이 서버의 커미션 카테고리를 선택해 주세요.')
        category_id = category.id
    if not category_id:
        raise UserError('기존 커미션 카테고리를 category 항목에서 선택해 주세요.')
    try:
        category = await guild.fetch_channel(category_id)
    except discord.NotFound as error:
        raise UserError('기존 커미션 카테고리를 category 항목에서 선택해 주세요.') from error
    if not isinstance(category, discord.CategoryChannel):
        raise UserError('커미션 카테고리를 선택해 주세요.')
    permissions = category.permissions_for(guild.me)
    if not all(getattr(permissions, key) for key in ('manage_channels', 'view_channel', 'send_messages', 'embed_links', 'read_message_history')):
        raise UserError('현재 카테고리 권한으로 봇이 후기 채널을 생성·사용할 수 없습니다. 소유자가 권한을 확인해 주세요.')
    saved = await bot.store.get(guild.id, CHANNEL_KEY)
    channel = None
    if saved:
        try:
            channel = await guild.fetch_channel(saved)
        except discord.NotFound:
            pass
        if channel and not isinstance(channel, discord.TextChannel):
            raise UserError('등록된 후기 채널의 종류를 확인해 주세요.')
    if channel is None:
        matches = [c for c in category.channels if c.name == '후기-reviews']
        if len(matches) > 1 or any(not isinstance(c, discord.TextChannel) for c in matches):
            raise UserError('커미션 카테고리의 후기-reviews 이름 중복·종류를 확인해 주세요.')
        channel = matches[0] if matches else None
    if (channel is None or channel.category_id != category.id) and len(category.channels) >= 50:
        raise UserError('커미션 카테고리의 채널 공간이 부족합니다.')
    if channel is None:
        channel = await guild.create_text_channel('후기-reviews', category=category,
            overwrites=category.overwrites, topic='완료된 의뢰의 신청자가 남긴 별점 후기 · 티켓의 후기 버튼 이용',
            reason='DDH 소유자 요청: 커미션 후기 채널 추가')
    # Only this feature's channel is synced. Existing categories, tickets,
    # roles, other channels and the commission hub are never edited here.
    await channel.edit(category=category, sync_permissions=True,
                       reason='DDH 소유자 요청: 후기 채널에 기존 커미션 카테고리 권한 동기화')
    await bot.store.set(guild.id, CHANNEL_KEY, channel.id)
    return channel


async def eligible_review(bot, guild, channel, actor):
    require_guild(guild, bot.settings)
    ticket = await bot.store.channel_ticket(guild.id, channel.id)
    if not ticket or ticket['kind'] != 'commission' or ticket['owner_id'] != actor.id:
        raise UserError('이 의뢰의 신청자만 후기를 작성할 수 있습니다.')
    if ticket['stage'] != 'delivered' or ticket['state'] not in ('open', 'closed') or not ticket.get('assigned_id'):
        raise UserError('담당 개발자 또는 운영진의 납품 완료 처리 후 후기를 작성할 수 있습니다.')
    actor = await guild.fetch_member(actor.id)
    member_id = await bot.store.get(guild.id, 'role:member')
    if (actor.bot or actor.pending or actor.is_timed_out()
            or not any(role.id == member_id for role in actor.roles)
            or not channel.permissions_for(actor).view_channel):
        raise UserError('멤버 인증과 의뢰 티켓 접근 권한을 확인해 주세요.')
    await ensure_private(bot, guild, channel, ticket)
    return ticket


async def send_once(bot, channel, key, card, after, *, view=None, content=None, allowed_mentions=None):
    """Recover a send-before-DB-write failure without posting the review twice."""
    marker = f'DDH:{key}:v1'
    card.set_footer(text=marker)
    # Search the full relevant interval, not the last 100 posts. This also
    # covers a crash after Discord accepted the send but before ID persistence.
    async for message in channel.history(limit=None, after=datetime.fromisoformat(after)):
        if message.author.id == channel.guild.me.id and any(e.footer.text == marker for e in message.embeds):
            return message
    return await channel.send(content=content, embed=card, view=view,
                              allowed_mentions=allowed_mentions or discord.AllowedMentions.none())


async def deliver_prompt(bot, guild, channel, row, *, resend=False):
    if (row['prompted_at'] and not resend) or row['rating'] is not None:
        return
    await review_channel(bot, guild)
    ticket = await bot.store.ticket(row['ticket_id'], guild.id)
    if (not ticket or ticket['stage'] != 'delivered' or ticket['state'] not in ('open', 'closed')
            or ticket.get('assigned_id') != row['developer_id']):
        return
    await ensure_private(bot, guild, channel, ticket)
    from ddh.review_views import ReviewRequestView
    card = embed('⭐ 납품 완료 · 별점 후기',
        f"<@{row['owner_id']}>님, 작업에 대한 1~5점 별점과 후기를 남겨 주세요.\n"
        '버튼 또는 `/ddh review`로 작성 · 의뢰당 1회 · 선택 사항\n'
        '작성하면 의뢰자 역할을 받습니다. 별점·후기는 후기 채널에 공개됩니다. 계좌·개인정보는 적지 마세요.')
    view = ReviewRequestView(bot)
    if resend:
        await channel.send(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
    else:
        key = f"review-request:{row['ticket_id']}:{row['developer_id']}"
        await send_once(bot, channel, key, card, row['requested_at'], view=view)
    await bot.store.mark_review(guild.id, row['ticket_id'])


async def request_review_by_command(bot, guild, channel, actor):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        ticket = await bot.store.channel_ticket(guild.id, channel.id)
        if not ticket or ticket['kind'] != 'commission' or ticket['state'] not in ('open', 'closed'):
            raise UserError('작품 의뢰 티켓에서 실행해 주세요.')
        await manager(bot, guild, actor, ticket)
        if ticket['stage'] != 'delivered' or not ticket.get('assigned_id'):
            raise UserError('납품 완료 후 요청할 수 있습니다. /ddh complete로 완료 처리해 주세요.')
        await ensure_private(bot, guild, channel, ticket)
        row = await bot.store.request_review(guild.id, ticket)
        if row['rating'] is not None:
            await deliver_review(bot, guild, row)
            return '이미 제출된 후기입니다. 티켓의 완료 알림에서 확인해 주세요.'
        await review_channel(bot, guild)
        key = f"review:manual:{ticket['id']}"
        now = datetime.now(timezone.utc).timestamp()
        last = await bot.store.get(guild.id, key, 0)
        if now - last < 30:
            raise UserError('후기 요청은 30초에 한 번 보낼 수 있습니다.')
        await bot.store.set(guild.id, key, now)
        await deliver_prompt(bot, guild, channel, row, resend=True)
        return '티켓 아래에 신청자용 후기 버튼을 보냈습니다. 신청자는 /ddh review로도 작성할 수 있습니다.'


async def request_review(bot, guild, channel, ticket):
    if ticket['kind'] != 'commission' or ticket['stage'] != 'delivered' or not ticket.get('assigned_id'):
        return
    row = await bot.store.request_review(guild.id, ticket)
    try:
        await deliver_prompt(bot, guild, channel, row)
    except (discord.HTTPException, UserError):
        # A missing review channel must not prevent completion/archival.
        log.info('Review prompt queued for ticket %s', ticket['id'])


async def deliver_review(bot, guild, row):
    if row['published_at']:
        await deliver_receipt(bot, guild, row)
        return row
    channel = await review_channel(bot, guild, row['channel_id'])
    stars = '★' * row['rating'] + '☆' * (5 - row['rating'])
    card = embed(f"{FIELDS[row['field']]} 제작 후기 · {row['rating']}/5점",
                 f"**{stars}**  ·  납품 완료 의뢰 #{row['ticket_id']:04d}")
    card.add_field(name='신청자', value=f"<@{row['owner_id']}>")
    card.add_field(name='담당 개발자', value=f"<@{row['developer_id']}>")
    # A Discord field is capped at 1024 characters. Leave user markdown
    # escaped, keeping the full review in the longer description instead.
    body = discord.utils.escape_mentions(discord.utils.escape_markdown(row['body'] or ''))
    if body:
        card.description += f"\n\n**신청자의 한마디**\n{body}"
    card.timestamp = datetime.fromisoformat(row['submitted_at'])
    message = await send_once(bot, channel, f"commission-review:{row['ticket_id']}", card, row['submitted_at'])
    await bot.store.mark_review(guild.id, row['ticket_id'], message_id=message.id)
    row = await bot.store.commission_review(guild.id, row['ticket_id'])
    await deliver_receipt(bot, guild, row)
    await bot.operations.record(guild, f"**의뢰 후기 등록 · #{row['ticket_id']}**\n작성자: <@{row['owner_id']}>\n"
        f"담당 개발자: <@{row['developer_id']}> · {FIELDS[row['field']]} · {row['rating']}/5점",
        key=f"review:published:{row['ticket_id']}")
    return await bot.store.commission_review(guild.id, row['ticket_id'])


async def deliver_receipt(bot, guild, row):
    if row.get('receipt_at') or not row['published_at']:
        return
    ticket = await bot.store.ticket(row['ticket_id'], guild.id)
    if not ticket or not ticket['channel_id']:
        return
    try:
        channel = await guild.fetch_channel(ticket['channel_id'])
    except discord.NotFound:
        # Public review stays published even if the owner removed the ticket.
        await bot.store.mark_review_receipt(guild.id, row['ticket_id'])
        return
    try:
        await ensure_private(bot, guild, channel, ticket)
        from ddh.review_views import ReviewReceiptView
        url = f"https://discord.com/channels/{guild.id}/{row['channel_id']}/{row['message_id']}"
        card = embed('✅ 별점 후기 작성 완료',
            f"**신청자:** <@{row['owner_id']}>\n**개발자:** <@{row['developer_id']}> · {FIELDS[row['field']]}\n"
            f"**별점:** {'⭐' * row['rating']} ({row['rating']}/5점)\n"
            + ('종료된 의뢰입니다. 후기 원문을 확인할 수 있습니다.' if ticket['state'] == 'closed'
               else '후기를 확인한 담당 개발자·운영진이 아래 버튼을 누르면 티켓 채널이 즉시 삭제됩니다.'))
        notify = ticket.get('assigned_id') == row['developer_id'] and ticket['state'] == 'open'
        await send_once(bot, channel, f"review-receipt:{row['ticket_id']}", card, row['submitted_at'],
            view=ReviewReceiptView(bot, url=url, closed=ticket['state'] == 'closed'),
            content=f"<@{row['developer_id']}> · 의뢰 후기가 등록되었습니다." if notify else None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                users=[discord.Object(id=row['developer_id'])] if notify else False))
        await bot.store.mark_review_receipt(guild.id, row['ticket_id'])
    except (discord.HTTPException, UserError):
        log.info('Review receipt pending for ticket %s', row['ticket_id'])


async def submit_review(bot, guild, channel, actor, rating, body, *, developer_id=None):
    require_guild(guild, bot.settings)
    if type(rating) is not int or not 1 <= rating <= 5 or not isinstance(body, str) or len(body) > 1000:
        raise UserError('별점은 1~5점, 후기 내용은 1,000자 이내로 작성해 주세요.')
    async with bot.mutation_lock:
        ticket = await eligible_review(bot, guild, channel, actor)
        if developer_id is not None and ticket['assigned_id'] != developer_id:
            raise UserError('담당자가 변경되었습니다. 현재 의뢰에서 후기를 다시 열어 주세요.')
        destination = await review_channel(bot, guild)
        row = await bot.store.request_review(guild.id, ticket)
        if row['rating'] is None:
            await bot.store.submit_review(guild.id, ticket['id'], rating, body.strip(), destination.id)
            row = await bot.store.commission_review(guild.id, ticket['id'])
        if await bot.store.pending_clients(guild.id, owner_id=ticket['owner_id']):
            await grant_client(bot, guild, ticket['owner_id'])
        try:
            return await deliver_review(bot, guild, row)
        except discord.HTTPException:
            log.warning('Review saved; delivery pending for ticket %s', ticket['id'])
            return row


async def recover_reviews(bot, guild):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        for owner_id in await bot.store.pending_clients(guild.id):
            await grant_client(bot, guild, owner_id)
        for row in await bot.store.pending_reviews(guild.id):
            try:
                if row['rating'] is not None:
                    await deliver_review(bot, guild, row)
                else:
                    ticket = await bot.store.ticket(row['ticket_id'], guild.id)
                    try:
                        channel = await guild.fetch_channel(ticket['channel_id'])
                    except discord.NotFound:
                        await bot.store.mark_review(guild.id, row['ticket_id'])
                        continue
                    await deliver_prompt(bot, guild, channel, row)
            except (discord.HTTPException, UserError):
                log.info('Review delivery pending for ticket %s', row['ticket_id'])
