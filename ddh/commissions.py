"""Private field-specific commissions, owner-installed and staff-assigned."""
import json
import re

import discord

from ddh.blueprint import FIELDS, access, overwrites
from ddh.content import embed, upsert_panel
from ddh.operations import person, private_channel
from ddh.server import UserError, channel_for, is_staff, require_guild, roles_for

# Five inputs fit Discord's modal limit. The field is kept as hidden metadata.
BRIEFS = {
    "gfx": ("장면·인물·분위기", "해상도·사용처·참고 이미지"),
    "ui": ("화면 목록·기능·디자인 스타일", "기기·해상도·소스 파일·구현 범위"),
    "clothing": ("상의·하의·종류·수량", "색상·로고·참고 디자인"),
    "scripting": ("기능·기대 동작·현재 환경", "기존 코드 연동·테스트·납품 범위"),
    "vfx": ("효과 종류·연출·지속 시간", "색상·성능 조건·참고 영상"),
    "building": ("맵 테마·규모·구역 구성", "참고 자료·최적화·납품 범위"),
    "modeling": ("모델 종류·수량·용도", "폴리곤·텍스처·리깅·파일 형식"),
    "animation": ("동작 목록·길이·루프 여부", "R6·R15·리그·참고 영상"),
}
STAGES = {"received": "접수·담당자 대기", "assigned": "담당자 배정", "quoted": "견적 안내",
          "working": "제작 중", "revision": "검수·수정", "delivered": "납품·완료 확인"}


def form_labels(field):
    if field not in BRIEFS:
        raise UserError("지원하는 개발 분야를 선택해 주세요.")
    return ("프로젝트·의뢰 제목", *BRIEFS[field], "예산·희망 납기", "사용 범위·추가 요청")


def validate_commission(fields):
    labels = form_labels(fields.get("_field"))
    if set(fields) != {"_field", *labels} or any(not isinstance(fields[label], str) or not fields[label].strip() for label in labels[:-1]):
        raise UserError("의뢰 제목, 분야별 작업 조건, 예산·납기를 작성해 주세요.")
    if any(len(str(value)) > 1000 for value in fields.values()):
        raise UserError("입력 내용이 너무 깁니다.")


async def developer_member(bot, guild, user_id, field=None):
    require_guild(guild, bot.settings)
    member = await guild.fetch_member(user_id)
    roles = await roles_for(guild, bot.store)
    eligible = roles[f"field:{field}"] in member.roles if field else (
        roles["developer"] in member.roles or any(roles[f"field:{key}"] in member.roles for key in FIELDS))
    if member.bot or member.pending or member.is_timed_out() or roles["member"] not in member.roles or not eligible:
        raise UserError("인증을 완료한 해당 분야 개발자만 사용할 수 있습니다.")
    return member


async def manager(bot, guild, actor, ticket):
    actor = await guild.fetch_member(actor.id)
    if await is_staff(actor, bot.store):
        return actor
    if ticket["kind"] != "commission" or ticket.get("assigned_id") != actor.id:
        raise UserError("운영진 또는 이 의뢰의 담당 개발자만 처리할 수 있습니다.")
    return await developer_member(bot, guild, actor.id, json.loads(ticket["fields"])["_field"])


async def save_account(bot, guild, actor, bank, account, holder):
    await developer_member(bot, guild, actor.id)
    bank, account, holder = bank.strip(), account.strip(), holder.strip()
    if not bank or not holder or max(len(bank), len(holder)) > 60 or not re.fullmatch(r"[0-9][0-9 -]{4,38}[0-9]", account):
        raise UserError("은행·예금주를 입력하고 계좌번호는 숫자·공백·하이픈으로 6~40자 입력해 주세요.")
    await bot.store.save_account(guild.id, actor.id, bank, account, holder)
    await bot.operations.record(guild, f"**개발자 계좌 등록·변경**\n담당자: {person(actor)}\n계좌 내용은 비공개로 저장되었습니다.")


async def ensure_private(bot, guild, channel, ticket, *, extra_ids=()):
    roles = await roles_for(guild, bot.store)
    permitted = {roles['staff'].id, roles['admin'].id, guild.me.id, ticket['owner_id'], ticket.get('assigned_id'), *extra_ids}
    if not private_channel(channel, guild, permitted):
        raise UserError("티켓이 외부에 공개될 수 있어 중단했습니다. 운영진이 비공개 권한을 확인해 주세요.")
    return roles


async def assign(bot, guild, channel, actor, developer):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        actor = await guild.fetch_member(actor.id)
        if not await is_staff(actor, bot.store):
            raise UserError("운영진만 담당 개발자를 배정할 수 있습니다.")
        ticket = await bot.tickets.current(guild, channel.id)
        if ticket['kind'] != 'commission':
            raise UserError("작품 의뢰 티켓 안에서 실행해 주세요.")
        field = json.loads(ticket['fields'])['_field']
        developer = await developer_member(bot, guild, developer.id, field)
        if developer.id == ticket['owner_id']:
            raise UserError("신청자 본인을 담당 개발자로 배정할 수 없습니다.")
        journal_key = f"commission:assignment:{ticket['id']}"
        pending = await bot.store.get(guild.id, journal_key)
        previous = pending.get('previous') if isinstance(pending, dict) and pending.get('target') == ticket.get('assigned_id') else ticket.get('assigned_id')
        await ensure_private(bot, guild, channel, ticket, extra_ids=(previous, developer.id))
        changed = previous != developer.id
        stage = 'assigned' if changed else ticket['stage']
        if changed and previous:
            await upsert_panel(channel, bot.store, f"quote:{ticket['id']}", embed('담당자 변경 안내',
                '담당 개발자가 변경되어 이전 계좌 안내를 중지했습니다. 새 담당자의 견적·계좌 안내를 기다려 주세요.'))
        await bot.store.set(guild.id, journal_key, {'previous': previous, 'target': developer.id})
        # Store desired assignee first. Explicit retry repairs an interrupted
        # permissions write; no background job rewrites owner channel edits.
        await bot.store.update_ticket(ticket['id'], guild.id, assigned_id=developer.id, stage=stage)
        roles = await roles_for(guild, bot.store)
        owner = await guild.fetch_member(ticket['owner_id'])
        policy = overwrites(guild, roles, 'private', owner=owner)
        policy[developer] = access()
        await channel.edit(overwrites=policy, reason=f"DDH 의뢰 #{ticket['id']} 담당자 배정")
        ticket.update(assigned_id=developer.id, stage=stage)
        await status_panel(bot, guild, channel, ticket)
        await bot.store.set(guild.id, journal_key, None)
        await bot.operations.record(guild, f"**의뢰 담당자 배정 · #{ticket['id']}**\n운영진: {person(actor)}\n담당 개발자: {person(developer)}\n분야: {FIELDS[field]} · {channel.mention}")


async def status_panel(bot, guild, channel, ticket):
    from ddh.commission_views import CommissionActions
    field = json.loads(ticket['fields'])['_field']
    assignee = f"<@{ticket['assigned_id']}>" if ticket.get('assigned_id') else '운영진 배정 대기'
    card = embed(f"{FIELDS[field]} 의뢰 #{ticket['id']} · 진행 현황",
                 f"**신청자:** <@{ticket['owner_id']}>\n**담당 개발자:** {assignee}\n"
                 f"**상태:** {STAGES.get(ticket.get('stage'), STAGES['received'])}\n\n"
                 "운영진이 `/ddh assign`으로 해당 분야 개발자를 배정합니다. 인력이 없는 분야는 대기하며 제작이 확정된 것이 아닙니다.\n"
                 "담당자는 `/ddh account`로 본인 계좌를 등록한 뒤 아래에서 견적·계좌를 안내할 수 있습니다.\n"
                 "금액·일정·수정 범위는 티켓에서 합의해 주세요. 입금 여부는 담당자가 직접 확인합니다.\n"
                 "신청자는 종료할 수 없으며 운영진·담당 개발자에게 종료를 요청할 수 있습니다.")
    await upsert_panel(channel, bot.store, f"commission:{ticket['id']}", card, view=CommissionActions(bot))


async def quote(bot, guild, channel, actor, amount, terms):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        ticket = await bot.tickets.current(guild, channel.id)
        if ticket['kind'] != 'commission' or ticket.get('assigned_id') != actor.id:
            raise UserError("배정된 담당 개발자만 본인의 견적·계좌를 안내할 수 있습니다.")
        actor = await developer_member(bot, guild, actor.id, json.loads(ticket['fields'])['_field'])
        await ensure_private(bot, guild, channel, ticket)
        account = await bot.store.account(guild.id, actor.id)
        if not account:
            raise UserError("먼저 `/ddh account`로 본인 계좌를 등록해 주세요.")
        digits = amount.replace(',', '').strip()
        if not digits.isascii() or not digits.isdecimal() or not 1 <= int(digits) <= 100_000_000 or not terms.strip() or len(terms) > 1000:
            raise UserError("금액은 원 단위 숫자로 입력하고 작업 범위·납기·수정 조건을 작성해 주세요.")
        card = embed(f"의뢰 #{ticket['id']} · 견적 및 입금 안내",
                     f"**담당자:** {person(actor)}\n**금액:** {int(digits):,}원\n\n"
                     f"**은행:** {discord.utils.escape_markdown(account['bank'])}\n"
                     f"**계좌번호:** {account['account']}\n**예금주:** {discord.utils.escape_markdown(account['holder'])}\n\n"
                     f"**협의 조건**\n{terms.strip()}\n\n입금 전 담당자·금액·조건을 확인해 주세요. 이 안내는 입금 완료 확인이 아닙니다.")
        # A repeat click updates the same private quote instead of duplicating
        # payment instructions. Account data never goes into operational logs.
        await upsert_panel(channel, bot.store, f"quote:{ticket['id']}", card)
        await bot.store.update_ticket(ticket['id'], guild.id, stage='quoted')
        ticket['stage'] = 'quoted'
        await status_panel(bot, guild, channel, ticket)
        await bot.operations.record(guild, f"**의뢰 견적 안내 · #{ticket['id']}**\n담당자: {person(actor)}\n{channel.mention} · 금액·계좌·협의 조건은 티켓 내부에서 확인")


async def progress(bot, guild, channel, actor, stage):
    require_guild(guild, bot.settings)
    async with bot.mutation_lock:
        ticket = await bot.tickets.current(guild, channel.id)
        if ticket['kind'] != 'commission' or not ticket.get('assigned_id') or stage not in ('working', 'revision', 'delivered'):
            raise UserError("담당자 배정 후 제작·검수·납품 상태를 선택해 주세요.")
        actor = await manager(bot, guild, actor, ticket)
        await ensure_private(bot, guild, channel, ticket)
        previous = STAGES[ticket['stage']]
        await bot.store.update_ticket(ticket['id'], guild.id, stage=stage)
        ticket['stage'] = stage
        await status_panel(bot, guild, channel, ticket)
        await bot.operations.record(guild, f"**의뢰 진행 변경 · #{ticket['id']}**\n처리자: {person(actor)}\n{previous} → {STAGES[stage]}\n{channel.mention}")


async def install(bot, guild):
    """Only the explicit owner command installs the panel and archives forum."""
    from ddh.content import publish_support
    roles = await roles_for(guild, bot.store)
    support = await channel_for(guild, bot.store, 'support_panel')
    old_id = await bot.store.get(guild.id, 'channel:commissions')
    old = guild.get_channel(old_id) if old_id else None
    if old:
        if not isinstance(old, discord.ForumChannel):
            raise UserError("기존 의뢰 포럼 연결이 달라졌습니다. 소유자가 채널 연결을 확인해 주세요.")
        policy = overwrites(guild, roles, 'readonly')
        policy[guild.default_role].view_channel = False
        policy[roles['member']] = access(write=False)
        await old.edit(name=old.name if old.name.startswith('보관-') else ('보관-' + old.name)[:100],
                       topic=f"기존 의뢰 기록 보관 · 새 의뢰는 {support.mention}의 작품 의뢰 버튼을 이용하세요.",
                       overwrites=policy, reason='DDH 소유자 요청: 의뢰 티켓으로 전환')
    await publish_support(bot, guild)
    await bot.store.set(guild.id, 'commissions:enabled', True)
    return "문의 채널에 분야별 의뢰 패널을 적용했습니다. 기존 의뢰 포럼은 기록을 보존하고 읽기 전용으로 전환했습니다."
