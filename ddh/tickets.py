"""Private support tickets with durable creation and review state."""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import discord

from ddh.blueprint import FIELDS, KINDS, access, overwrites
from ddh.channels import fetch_supported_channels
from ddh.content import embed, upsert_panel
from ddh.operations import person, private_channel
from ddh.server import UserError, is_staff, require_guild, roles_for, ticket_category, unsafe_role_permissions

log = logging.getLogger(__name__)
PORTFOLIO_LINK = "포트폴리오 링크"


def validate_payload(kind, fields):
    if kind not in KINDS:
        raise UserError("지원하지 않는 문의 종류입니다.")
    if kind == "commission":
        from ddh.commissions import validate_commission
        return validate_commission(fields)
    required = {
        "partner": ("서버 이름", "초대 링크", "서버 소개·제휴 제안"),
        "feedback": ("제목", "내용"),
        "developer": ("Roblox 사용자명·프로필", "경력·도구·본인 담당 부분"),
    }[kind]
    if any(not str(fields.get(key, "")).strip() for key in required):
        raise UserError("필수 항목을 모두 작성해 주세요.")
    if any(len(str(value)) > 1000 for value in fields.values()) or len(fields) > 6:
        raise UserError("입력 내용이 너무 깁니다.")
    if kind == "developer":
        selected = fields.get("_fields", [])
        if not isinstance(selected, list) or not selected or set(selected) - FIELDS.keys():
            raise UserError("지원할 개발 분야를 선택해 주세요.")
    link_key = {"partner": "초대 링크", "developer": PORTFOLIO_LINK}.get(kind)
    links = str(fields.get(link_key, "")).split() if link_key else []
    if links:
        for url in links:
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.scheme in ("https", "http") and parsed.netloc:
                break
        else:
            raise UserError("http:// 또는 https://로 시작하는 링크를 입력해 주세요.")


def ticket_channel_name(ticket, *, closed=False):
    kind = ticket['kind']
    if kind == 'commission':
        field = json.loads(ticket['fields'])['_field']
        if field not in FIELDS:
            raise UserError('의뢰 분야를 확인해 주세요.')
        kind = field
    return f"{'종료-' if closed else ''}{kind}-{ticket['id']:04d}"


class Tickets:
    def __init__(self, bot):
        self.bot = bot
        self.store = bot.store

    async def ready(self, guild):
        require_guild(guild, self.bot.settings)
        if not await self.store.get(guild.id, "setup_complete", False):
            raise UserError("관리자의 DDH 서버 구성이 아직 완료되지 않았습니다.")

    async def audit(self, guild, text, *, key=None):
        try:
            await self.bot.operations.record(guild, text, key=key)
        except Exception:
            log.exception("DDH ticket audit recording failed for guild %s", guild.id)

    async def create(self, guild, member, kind, fields):
        await self.ready(guild)
        validate_payload(kind, fields)
        async with self.bot.mutation_lock:
            await self.ready(guild)
            try:
                member = await guild.fetch_member(member.id)
            except discord.NotFound:
                raise UserError("현재 DDH 멤버만 문의를 열 수 있습니다.") from None
            if member.pending or member.is_timed_out():
                raise UserError("Discord 규칙 확인과 멤버 인증을 완료한 뒤 문의를 열어 주세요.")
            roles = await roles_for(guild, self.store)
            if roles["member"] not in member.roles and not await is_staff(member, self.store):
                raise UserError("먼저 인증 채널에서 멤버 인증을 완료해 주세요.")
            existing = await self.store.active(guild.id, member.id, kind)
            if existing:
                return await self.materialize(guild, existing, member, roles)
            last = await self.store.get(guild.id, f"ticket_cooldown:{member.id}")
            if last and datetime.now(timezone.utc).timestamp() - last < 60:
                raise UserError("새 티켓은 1분에 한 번 만들 수 있습니다.")
            assigned_id = fields.get("_developer") if kind == "commission" else None
            if assigned_id is not None:
                from ddh.commissions import developer_member
                if assigned_id == member.id:
                    raise UserError("본인을 의뢰 담당자로 선택할 수 없습니다.")
                await developer_member(self.bot, guild, assigned_id, fields["_field"])
            ticket = await self.store.reserve(guild.id, member.id, kind, fields, assigned_id=assigned_id)
            await self.store.set(guild.id, f"ticket_cooldown:{member.id}", datetime.now(timezone.utc).timestamp())
            return await self.materialize(guild, ticket, member, roles)

    async def materialize(self, guild, ticket, member, roles):
        """Recover creation interrupted before the channel ID/first message was saved."""
        marker = f"DDH ticket:{ticket['id']} owner:{ticket['owner_id']} kind:{ticket['kind']}"
        channels = await fetch_supported_channels(guild)
        channel = next((c for c in channels if isinstance(c, discord.TextChannel) and
                        (c.id == ticket["channel_id"] or c.topic == marker)), None)
        if channel is None and ticket["state"] == "open":
            await self.store.update_ticket(ticket["id"], guild.id, state="deleted")
            raise UserError("이전 티켓 채널이 삭제되었습니다. 문의를 다시 신청해 주세요.")
        permitted = {roles["staff"].id, roles["admin"].id, guild.me.id, member.id}
        expected = overwrites(guild, roles, "private", owner=member)
        if ticket.get("assigned_id"):
            permitted.add(ticket["assigned_id"])
        if channel is None:
            if ticket.get("assigned_id"):
                from ddh.commissions import developer_member
                try:
                    developer = await developer_member(self.bot, guild, ticket["assigned_id"], json.loads(ticket["fields"])["_field"])
                except UserError:
                    # If the chosen developer leaves/loses eligibility between
                    # reservation and creation, retain the request for staff.
                    permitted.discard(ticket["assigned_id"])
                    await self.store.update_ticket(ticket["id"], guild.id, assigned_id=None, stage="received")
                    ticket.update(assigned_id=None, stage="received")
                else:
                    expected[developer] = access()
            category = await ticket_category(guild, self.store, kind=ticket["kind"])
            channel = await guild.create_text_channel(
                ticket_channel_name(ticket), category=category, topic=marker,
                overwrites=expected,
                reason=f"DDH {KINDS[ticket['kind']]} #{ticket['id']}",
            )
        if not private_channel(channel, guild, permitted):
            raise UserError("티켓 채널의 비공개 권한을 확인해 주세요. 운영진이 권한을 직접 수정한 뒤 다시 시도할 수 있습니다.")
        await self.store.update_ticket(ticket["id"], guild.id, channel_id=channel.id)
        await self.publish_ticket(guild, channel, ticket)
        if ticket["state"] == "creating":
            await self.store.update_ticket(ticket["id"], guild.id, state="open")
            assigned = f"\n담당 개발자: <@{ticket['assigned_id']}>" if ticket.get("assigned_id") else ""
            await self.audit(guild, f"**{KINDS[ticket['kind']]} 접수 · #{ticket['id']}**\n신청자: {person(member)}{assigned}\n{channel.mention}")
        return channel

    async def publish_ticket(self, guild, channel, ticket):
        from ddh.views import TicketActions
        fields = json.loads(ticket["fields"])
        if ticket['kind'] == 'commission':
            card = embed(f"{FIELDS[fields['_field']]} 의뢰 #{ticket['id']} · 신청 내용",
                f"**신청자:** <@{ticket['owner_id']}>\n추가 자료는 여기에 첨부하고 담당자와 상담해 주세요.")
        else:
            attachment_guide = (
                "포트폴리오 링크가 없다면 이 티켓에 작업 이미지·영상·PDF를 첨부해 주세요. "
                "공동 작업은 본인이 맡은 부분을 함께 적어 주세요."
                if ticket["kind"] == "developer" else "추가 자료는 이 채널에 첨부해 주세요."
            )
            card = embed(f"{KINDS[ticket['kind']]} #{ticket['id']}",
                         f"신청자: <@{ticket['owner_id']}>\n담당자가 신청서를 확인 후 답변합니다. "
                         f"{attachment_guide} 비밀번호·토큰은 보내지 마세요.\n"
                         "승인·반려·종료는 운영진 전용입니다. 종료 시 신청·처리 기록은 DB에 남고 채널은 삭제됩니다.")
        for key, value in fields.items():
            if key in ("_developer", "_field"):
                continue
            if key == "_fields":
                card.add_field(name="지원 분야", value=" / ".join(FIELDS[f] for f in value), inline=False)
            elif key != PORTFOLIO_LINK or value:
                card.add_field(name=key, value=value or "미입력", inline=False)
        await upsert_panel(channel, self.store, f"ticket:{ticket['id']}", card, view=None if ticket["kind"] == "commission" else TicketActions(self.bot))
        if ticket["kind"] == "commission":
            from ddh.commissions import status_panel
            await status_panel(self.bot, guild, channel, ticket)

    async def current(self, guild, channel_id):
        await self.ready(guild)
        ticket = await self.store.channel_ticket(guild.id, channel_id)
        if not ticket or ticket["state"] != "open":
            raise UserError("진행 중인 DDH 문의 티켓에서만 사용할 수 있습니다.")
        return ticket

    async def review(self, guild, channel, reviewer, approved, reason):
        await self.ready(guild)
        reviewer = await guild.fetch_member(reviewer.id)
        if not await is_staff(reviewer, self.store):
            raise UserError("운영진만 신청을 심사할 수 있습니다.")
        async with self.bot.mutation_lock:
            ticket = await self.current(guild, channel.id)
            if ticket["kind"] not in ("partner", "developer"):
                raise UserError("개발자·파트너 신청만 승인·반려할 수 있습니다.")
            if ticket["decision"] == "approving" and not approved:
                raise UserError("역할 지급이 진행 중입니다. 승인 버튼으로 재시도하거나 자동 복구를 기다려 주세요.")
            if ticket["decision"] not in ("pending", "approving"):
                raise UserError("이미 심사가 완료된 신청입니다.")
            if approved:
                if ticket["decision"] == "pending":
                    # Journal intent before any role grant. A partial grant cannot
                    # subsequently be marked rejected while leaving roles behind.
                    await self.store.update_ticket(ticket["id"], guild.id, decision="approving",
                                                   reviewer_id=reviewer.id, reason=reason)
                    ticket.update(decision="approving", reviewer_id=reviewer.id, reason=reason)
                return await self.finish_approval(guild, channel, ticket)
            await self.store.update_ticket(ticket["id"], guild.id, decision="rejected",
                                           reviewer_id=reviewer.id, reason=reason)
            await self.review_notice(guild, channel, ticket, "반려", reason, reviewer.id)
            return "반려"

    async def review_notice(self, guild, channel, ticket, label, reason, reviewer_id):
        await self.audit(guild, f"**신청 #{ticket['id']} {label}**\n심사자: <@{reviewer_id}>\n사유: {reason}")
        try:
            await channel.send(embed=embed(f"신청 {label}", reason), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Review saved, result delivery failed for ticket %s", ticket["id"])

    async def finish_approval(self, guild, channel, ticket):
        roles = await roles_for(guild, self.store)
        try:
            member = await guild.fetch_member(ticket["owner_id"])
        except discord.NotFound:
            reason = "신청자가 서버를 나가 승인을 취소했습니다."
            await self.store.update_ticket(ticket["id"], guild.id, decision="rejected", reason=reason)
            await self.review_notice(guild, channel, ticket, "반려", reason, ticket["reviewer_id"])
            return "반려"
        if member.pending or member.is_timed_out() or roles["member"] not in member.roles:
            raise UserError("신청자의 멤버 인증과 이용 상태를 확인한 뒤 승인을 다시 시도해 주세요.")
        keys = ["member", "partner"] if ticket["kind"] == "partner" else [
            "member", "developer", *[f"field:{f}" for f in json.loads(ticket["fields"])["_fields"]]]
        grants = [roles[key] for key in keys]
        if any(r >= guild.me.top_role or r.managed or unsafe_role_permissions(r) for r in grants):
            raise UserError("지급할 역할의 권한·순서를 점검해 주세요. 봇 역할이 대상 역할보다 높아야 합니다.")
        if not guild.me.guild_permissions.manage_roles:
            raise UserError("Dialian에 역할 관리 권한이 필요합니다.")
        await member.add_roles(*grants, reason=f"DDH 신청 #{ticket['id']} 승인 · {ticket['reviewer_id']}")
        await self.store.update_ticket(ticket["id"], guild.id, decision="approved")
        await self.review_notice(guild, channel, ticket, "승인", ticket["reason"], ticket["reviewer_id"])
        return "승인"

    async def close(self, guild, channel, actor, reason):
        await self.ready(guild)
        async with self.bot.mutation_lock:
            ticket = await self.current(guild, channel.id)
            from ddh.commissions import manager
            await manager(self.bot, guild, actor, ticket)
            if ticket["decision"] == "approving":
                raise UserError("역할 지급이 진행 중입니다. 운영진이 역할 권한을 확인하고 승인을 완료한 뒤 종료해 주세요.")
            roles = await roles_for(guild, self.store)
            # Journal before deleting: an interrupted close is completed on restart.
            await self.store.update_ticket(ticket["id"], guild.id, state="closing", close_reason=reason, closed_by=actor.id)
            ticket["closed_by"] = actor.id
            ticket["close_reason"] = reason
            await self.finish_close(guild, channel, ticket, roles)

    async def finish_close(self, guild, channel, ticket, roles):
        if ticket["kind"] == "commission":
            from ddh.blueprint import access
            from ddh.commissions import ensure_private
            await ensure_private(self.bot, guild, channel, ticket)
            policy = channel.overwrites
            for target in list(policy):
                if target.id in (ticket["owner_id"], ticket.get("assigned_id")):
                    policy[target] = access(write=False)
            name = ticket_channel_name(ticket, closed=True) if channel.name in (
                ticket_channel_name(ticket), f"commission-{ticket['id']:04d}") else channel.name
            await channel.edit(name=name, overwrites=policy, reason="DDH 의뢰 종료·대화 기록 보존")
            await self.complete_close(guild, ticket)
            from ddh.reviews import request_review
            await request_review(self.bot, guild, channel, ticket)
            return
        await channel.edit(
            name=f"closed-{ticket['kind']}-{ticket['id']:04d}",
            overwrites=overwrites(guild, roles, "private"),
            reason="DDH 문의 종료 및 비공개 처리",
        )
        # Closing was recorded first, so an interrupted deletion is retryable.
        await channel.delete(reason="DDH 문의 종료: 티켓 카테고리 없이 정리")
        await self.complete_close(guild, ticket)

    async def complete_close(self, guild, ticket):
        await self.audit(guild, f"**티켓 #{ticket['id']} 종료**\n처리자: {'<@' + str(ticket['closed_by']) + '>' if ticket['closed_by'] else '자동 복구'}\n사유: {ticket.get('close_reason') or '기록 없음'}",
                         key=f"ticket:closed:{ticket['id']}")
        await self.store.update_ticket(ticket["id"], guild.id, state="closed")

    async def recover(self, guild):
        await self.ready(guild)
        async with self.bot.mutation_lock:
            roles = await roles_for(guild, self.store)
            for ticket in await self.store.tickets(guild.id, "creating"):
                try:
                    member = await guild.fetch_member(ticket["owner_id"])
                    await self.materialize(guild, ticket, member, roles)
                except discord.NotFound:
                    # Preserve a created orphan privately if the applicant left.
                    channels = await fetch_supported_channels(guild)
                    channel = next((c for c in channels if c.id == ticket["channel_id"] or
                                    getattr(c, "topic", "") == f"DDH ticket:{ticket['id']} owner:{ticket['owner_id']} kind:{ticket['kind']}"), None)
                    if channel:
                        await self.store.update_ticket(ticket["id"], guild.id, channel_id=channel.id, state="closing")
                    else:
                        await self.store.update_ticket(ticket["id"], guild.id, state="deleted")
                except (discord.HTTPException, UserError):
                    log.exception("Ticket creation recovery deferred for %s", ticket["id"])
            for ticket in await self.store.tickets(guild.id, "closing"):
                try:
                    channel = await guild.fetch_channel(ticket["channel_id"])
                except discord.NotFound:
                    # The channel may have been deleted after a completed close,
                    # but before the final database update.
                    await self.complete_close(guild, ticket)
                    continue
                try:
                    await self.finish_close(guild, channel, ticket, roles)
                except (discord.HTTPException, UserError):
                    log.exception("Ticket close recovery deferred for %s", ticket["id"])
            for ticket in await self.store.approvals(guild.id):
                try:
                    channel = await guild.fetch_channel(ticket["channel_id"])
                except discord.NotFound:
                    await self.store.update_ticket(ticket["id"], guild.id, state="deleted")
                    continue
                try:
                    await self.finish_approval(guild, channel, ticket)
                except (discord.HTTPException, UserError):
                    log.exception("Approval recovery deferred for ticket %s", ticket["id"])
