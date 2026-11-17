"""One owner-installed place for commission instructions, pricing and intake."""
import discord

from ddh.blueprint import CATEGORIES, CHANNELS, commission_hub_overwrites
from ddh.content import embed, upsert_panel
from ddh.operations import private_channel
from ddh.server import UserError, find_resource


async def publish_commission_hub(bot, guild, channel=None):
    from ddh.commission_views import CommissionEntryView
    channel_id = await bot.store.get(guild.id, 'channel:commission_panel')
    channel = channel or (guild.get_channel(channel_id) if channel_id else None)
    if not isinstance(channel, discord.TextChannel):
        return None
    card = embed('🛒 DDH · 작품 제작 의뢰',
                 '원하는 작업을 선택하고 개발자와 직접 상담하세요.\n'
                 '**GFX · UI · 복장 · 스크립팅 · VFX · 빌딩 · 모델링 · 애니메이션**')
    card.add_field(name='① 신청 방법', value=(
        '**아래 「작품 의뢰 시작」 → 분야 → 개발자 선택 → 간단 신청서**\n'
        '신청서는 의뢰 제목과 작업 내용 중심입니다. 참고 자료는 선택 사항이며, '
        '티켓이 열린 뒤 이미지·파일을 첨부해도 됩니다.'), inline=False)
    card.add_field(name='② 가격 안내', value=(
        '**공통 고정 가격표는 없습니다.** 개발자·작업 종류·수량·난이도에 따라 가격이 달라집니다.\n'
        '담당 개발자가 신청서를 확인한 뒤 티켓에서 견적을 안내합니다. '
        '**최종 금액·일정·수정 조건을 확인하고 합의한 뒤 진행하세요.**'), inline=False)
    card.add_field(name='③ 접수 후 진행', value=(
        '선택한 개발자가 비공개 티켓에서 신청서를 읽고 손님과 상담합니다.\n'
        '**상담·견적 → 조건 합의 → 제작 → 검수·수정 → 납품**\n'
        '입금 계좌는 담당 개발자가 해당 티켓에서 안내하며 입금 여부도 직접 확인합니다.'), inline=False)
    card.add_field(name='④ 이용 안내', value=(
        '• 신청자·담당 개발자·운영진만 의뢰 내용을 볼 수 있습니다.\n'
        '• 원하는 분야에 인력이 없으면 운영진에게 배정을 요청할 수 있습니다.\n'
        '• 이미 진행 중인 의뢰가 있으면 기존 티켓으로 연결됩니다.\n'
        '• 종료·취소는 담당 개발자나 운영진에게 요청하세요. 일반 신청자는 직접 종료할 수 없습니다.'), inline=False)
    portfolio_id = await bot.store.get(guild.id, 'channel:portfolio')
    if portfolio_id and guild.get_channel(portfolio_id):
        card.add_field(name='개발자 작업물 살펴보기', value=f'<#{portfolio_id}>에서 포트폴리오를 확인하세요.', inline=False)
    return await upsert_panel(channel, bot.store, 'commission-hub', card, view=CommissionEntryView(bot))


async def configure_hub(bot, guild, roles):
    """Create only this area; keep ticket ACLs and existing messages intact."""
    category_spec = next(item for item in CATEGORIES if item.key == 'commissions')
    channel_spec = next(item for item in CHANNELS if item.key == 'commission_panel')
    category = await find_resource(guild, bot.store, 'category:commissions', category_spec.name, discord.CategoryChannel)
    channel = await find_resource(guild, bot.store, 'channel:commission_panel', channel_spec.name, discord.TextChannel)
    if not guild.me.guild_permissions.manage_channels:
        raise UserError('의뢰 공간을 구성하려면 봇에 채널 관리 권한이 필요합니다.')
    if len(guild.channels) + int(category is None) + int(channel is None) > 500:
        raise UserError('의뢰 안내 채널을 만들 서버 채널 공간이 부족합니다.')
    moving = []
    for state in ('creating', 'open'):
        for ticket in await bot.store.tickets(guild.id, state):
            if ticket['kind'] != 'commission':
                continue
            existing = guild.get_channel(ticket['channel_id']) if ticket['channel_id'] else None
            if existing and (category is None or existing.category_id != category.id):
                permitted = {roles['staff'].id, roles['admin'].id, guild.me.id, ticket['owner_id'], ticket.get('assigned_id')}
                if not private_channel(existing, guild, permitted):
                    raise UserError(f"의뢰 #{ticket['id']} 비공개 권한을 확인한 뒤 다시 실행해 주세요.")
                moving.append(existing)
    if (len(category.channels) if category else 0) + len(moving) + int(channel is None) > 50:
        raise UserError('진행 중 의뢰를 모을 카테고리 공간이 부족합니다. 종료된 티켓을 정리해 주세요.')
    policy = commission_hub_overwrites(guild, roles)
    if category is None:
        category = await guild.create_category(category_spec.name, overwrites=policy, reason='DDH 소유자 요청: 의뢰 전용 공간')
    await bot.store.set(guild.id, 'category:commissions', category.id)
    if channel is None:
        channel = await guild.create_text_channel(channel_spec.name, category=category, topic=channel_spec.topic,
                                                 overwrites=policy, reason='DDH 소유자 요청: 의뢰 안내·접수')
    else:
        # Names, ordering and manually chosen category stay owner-controlled.
        await channel.edit(overwrites=policy, reason='DDH 소유자 요청: 의뢰 안내 읽기 권한')
    await bot.store.set(guild.id, 'channel:commission_panel', channel.id)
    for ticket_channel in moving:
        await ticket_channel.edit(category=category, sync_permissions=False, reason='DDH 소유자 요청: 의뢰 티켓을 전용 공간으로 이동')
    await publish_commission_hub(bot, guild, channel)
    return channel
