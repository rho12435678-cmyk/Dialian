"""Durable operational events and read-only policy checks for DDH."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import discord

from ddh.audit_details import describe_audit, identity
from ddh.blueprint import CATEGORIES, CHANNELS, ROLES, category_policy, overwrites, role_permissions
from ddh.security import specifications
from ddh.server import REQUIRED_PERMISSIONS, forum_permissions

log = logging.getLogger(__name__)

RISKY_ACTIONS = {
    "channel_delete", "role_delete", "overwrite_create", "overwrite_update",
    "overwrite_delete", "webhook_create", "webhook_update", "webhook_delete",
    "guild_update", "automod_rule_create", "automod_rule_update", "automod_rule_delete",
    "app_command_permission_update", "bot_add", "kick", "ban", "unban", "member_prune",
    "integration_create", "integration_update", "integration_delete",
}
AUTOMOD_ACTIONS = {"automod_block_message", "automod_flag_message", "automod_timeout_member"}
AUDIT_LABELS = {
    "guild_update": "서버 설정 변경", "channel_create": "채널 생성", "channel_update": "채널 변경",
    "channel_delete": "채널 삭제", "overwrite_create": "채널 권한 추가",
    "overwrite_update": "채널 권한 변경", "overwrite_delete": "채널 권한 삭제",
    "role_create": "역할 생성", "role_update": "역할 변경", "role_delete": "역할 삭제",
    "member_role_update": "멤버 역할 변경", "member_update": "멤버 관리 변경",
    "bot_add": "봇 추가", "kick": "추방", "ban": "차단", "unban": "차단 해제",
    "member_prune": "비활성 멤버 정리", "invite_create": "초대 생성", "invite_delete": "초대 삭제",
    "invite_update": "초대 설정 변경", "member_move": "음성 채널 이동", "member_disconnect": "음성 연결 해제",
    "integration_create": "연동 추가", "integration_update": "연동 변경", "integration_delete": "연동 삭제",
    "webhook_create": "웹훅 생성", "webhook_update": "웹훅 변경", "webhook_delete": "웹훅 삭제",
    "thread_create": "포럼 게시글 생성 감사 기록", "thread_update": "게시글 설정 변경", "thread_delete": "게시글 삭제",
    "automod_rule_create": "AutoMod 규칙 생성", "automod_rule_update": "AutoMod 규칙 변경",
    "automod_rule_delete": "AutoMod 규칙 삭제", "app_command_permission_update": "앱 명령 권한 변경",
    "automod_block_message": "AutoMod · 메시지 차단", "automod_flag_message": "AutoMod · 메시지 감지",
    "automod_timeout_member": "AutoMod · 멤버 타임아웃",
    "message_delete": "메시지 삭제 감사 기록", "message_bulk_delete": "메시지 일괄 삭제 감사 기록",
    "message_pin": "메시지 고정", "message_unpin": "메시지 고정 해제",
}


def private_channel(channel, guild, permitted):
    return (
        isinstance(channel, discord.TextChannel)
        and channel.overwrites_for(guild.default_role).view_channel is False
        and not any(target.id not in permitted and overwrite.view_channel is True
                    for target, overwrite in channel.overwrites.items())
    )


async def inspect_layout(guild, store):
    state = await store.settings(guild.id)
    findings = []
    roles = {key: guild.get_role(state.get(f"role:{key}", 0)) for key in ROLES}
    for key, role in roles.items():
        if role is None:
            findings.append(f"역할 누락 · {ROLES[key][0]}")
        elif role.permissions != role_permissions(key):
            findings.append(f"역할 권한 불일치 · {ROLES[key][0]}")
    if guild.me is None:
        return findings + ["봇 서버 멤버 정보 확인 불가"]
    for permission in REQUIRED_PERMISSIONS:
        if not getattr(guild.me.guild_permissions, permission):
            findings.append(f"봇 권한 누락 · {permission}")
    if all(roles[key] for key in ("staff", "admin", "developer")):
        if not roles["staff"] > roles["admin"] > roles["developer"]:
            findings.append("역할 순서 확인 · Staff > Admin > Developer")
    if any(role and role >= guild.me.top_role for key, role in roles.items() if key not in ("staff", "admin")):
        findings.append("봇 역할 순서 확인 · 인증·신청 지급 대상 역할보다 Dialian을 위로 이동")
    for spec in CATEGORIES:
        channel = guild.get_channel(state.get(f"category:{spec.key}", 0))
        if not isinstance(channel, discord.CategoryChannel):
            findings.append(f"카테고리 누락 · {spec.name}")
        elif roles["staff"] and roles["admin"] and roles["member"]:
            if channel.overwrites != overwrites(guild, roles, category_policy(spec)):
                findings.append(f"카테고리 권한 불일치 · {spec.name}")
    for spec in CHANNELS:
        channel = guild.get_channel(state.get(f"channel:{spec.key}", 0))
        expected = discord.ForumChannel if spec.forum else discord.VoiceChannel if spec.voice else discord.TextChannel
        if not isinstance(channel, expected):
            findings.append(f"채널 누락 또는 형식 불일치 · {spec.name}")
            continue
        if spec.news and channel.type != discord.ChannelType.news:
            findings.append(f"공지 형식 확인 · {spec.name}")
        if spec.forum and not channel.available_tags:
            findings.append(f"포럼 태그 누락 · {spec.name}")
        if spec.policy in ("private", "log") and isinstance(channel, discord.TextChannel):
            permitted = {state.get("role:staff"), state.get("role:admin"), guild.me.id}
            if not private_channel(channel, guild, permitted):
                outsiders = [identity(target) for target, override in channel.overwrites.items()
                             if target.id not in permitted and override.view_channel is True]
                reason = ", ".join(outsiders[:3])
                if channel.overwrites_for(guild.default_role).view_channel is not False:
                    reason = "@everyone 채널 보기 차단 없음" + (f" / {reason}" if reason else "")
                findings.insert(0, f"비공개 채널 접근 확인 · {identity(channel)} · {reason}")
        required = ("member", "external_bots") if spec.policy == "commands" else ()
        if (roles["staff"] and roles["admin"] and roles["member"] and all(roles[key] for key in required)
                and (spec.policy not in roles or roles[spec.policy])):
            policy = forum_permissions(guild, roles, spec.policy) if spec.forum else overwrites(
                guild, roles, spec.policy, voice=spec.voice)
            if spec.key == "updates":
                policy[guild.me].mention_everyone = True
            if channel.overwrites != policy:
                findings.append(f"채널 권한 불일치 · {spec.name}")
    for ticket in await store.tickets(guild.id, "open"):
        channel = guild.get_channel(ticket["channel_id"])
        permitted = {state.get("role:staff"), state.get("role:admin"), guild.me.id, ticket["owner_id"]}
        if not private_channel(channel, guild, permitted):
            findings.append(f"티켓 접근 범위 확인 · #{ticket['id']}")
    if getattr(guild, "verification_level", discord.VerificationLevel.medium).value < discord.VerificationLevel.medium.value:
        findings.append("Discord 서버 인증 수준 확인 · Medium 이상")
    if getattr(guild, "explicit_content_filter", discord.ContentFilter.all_members) != discord.ContentFilter.all_members:
        findings.append("Discord 콘텐츠 필터 확인 · 모든 멤버")
    return findings


async def inspect_automod(guild, store):
    findings = []
    try:
        rules = await guild.fetch_automod_rules()
    except discord.HTTPException:
        return ["AutoMod 규칙 조회 실패 · 봇 서버 관리 권한 확인"]
    security_id = await store.get(guild.id, "channel:security_log")
    for name, trigger, _ in specifications():
        managed = next((rule for rule in rules if rule.name == name), None)
        candidates = [managed] if managed else [
            rule for rule in rules
            if trigger.type != discord.AutoModRuleTriggerType.keyword and rule.trigger_type == trigger.type]
        if not any(rule.enabled and rule.trigger_type == trigger.type
                   and any(action.type == discord.AutoModRuleActionType.block_message for action in rule.actions)
                   for rule in candidates):
            findings.append(f"AutoMod 차단 설정 확인 · {name}")
        if managed and not any(action.type == discord.AutoModRuleActionType.send_alert_message
                               and action.channel_id == security_id for action in managed.actions):
            findings.append(f"AutoMod 보안 로그 연결 확인 · {name}")
    return findings


class Operations:
    def __init__(self, bot):
        self.bot = bot
        self.store = bot.store
        self.delivery_lock = asyncio.Lock()

    async def record(self, guild, body, *, key=None, kind="operation", sink="audit", at=None, immediate=True):
        if guild.id != self.bot.settings.guild_id:
            return False
        at = at or datetime.now(timezone.utc)
        added = await self.store.record_event(
            guild.id, key or uuid4().hex, sink, kind, body[:800], at.isoformat())
        if added and immediate:
            await self.flush(guild)
        return added

    async def flush(self, guild):
        if guild.id != self.bot.settings.guild_id or self.delivery_lock.locked():
            return
        async with self.delivery_lock:
            state = await self.store.settings(guild.id)
            if guild.me is None or not state.get("role:staff") or not state.get("role:admin"):
                return
            permitted = {state["role:staff"], state["role:admin"], guild.me.id}
            for sink, title, colour in (("member_log", "DDH 입퇴장", 0x5865F2),
                                        ("security_log", "DDH 보안 기록", 0xC84242),
                                        ("audit", "DDH 운영 기록", 0x387BC4)):
                rows = await self.store.pending_events(guild.id, sink, limit=4)
                if not rows:
                    continue
                channel = guild.get_channel(state.get(f"channel:{sink}", 0))
                if not private_channel(channel, guild, permitted):
                    log.warning("DDH log sink %s unavailable or has unsafe overwrites", sink)
                    continue
                lines = []
                for row in rows:
                    occurred = int(datetime.fromisoformat(row["occurred_at"]).timestamp())
                    lines.append(f"<t:{occurred}:d> <t:{occurred}:T> · {row['body']}")
                card = discord.Embed(title=title, description="\n\n".join(lines), colour=colour)
                try:
                    await channel.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    log.exception("DDH %s delivery deferred", sink)
                    continue
                await self.store.mark_delivered(guild.id, rows)

    async def membership(self, guild, user, *, joined):
        if guild.id != self.bot.settings.guild_id:
            return
        now = datetime.now(timezone.utc)
        sink = "member_log" if await self.store.get(guild.id, "channel:member_log") else "audit"
        joined_at = getattr(user, "joined_at", None)
        key = f"join:{user.id}:{joined_at.isoformat()}" if joined and joined_at else None
        label = "입장" if joined else "탈퇴·퇴장"
        added = await self.record(guild, f"{label} · {identity(user, name=user.name)}",
                                  key=key, kind="join" if joined else "leave", sink=sink, at=now, immediate=False)
        if added and joined:
            await self.burst(guild, "join", 5, "60초 내 입장 5건 이상", now)
        await self.flush(guild)

    async def burst(self, guild, kind, threshold, label, now):
        count = await self.store.count_events(guild.id, kind, (now - timedelta(seconds=60)).isoformat())
        if count >= threshold:
            await self.record(guild, f"{label} · 최근 {count}건 · 운영진 확인 필요",
                              key=f"burst:{kind}:{int(now.timestamp()) // 60}", sink="security_log",
                              immediate=False, at=now)

    async def audit(self, entry, *, immediate=True):
        guild = entry.guild
        if guild.id != self.bot.settings.guild_id or (guild.me is not None and entry.user_id == guild.me.id):
            return
        action = entry.action.name
        label = AUDIT_LABELS.get(action)
        if label is None:
            return
        details, sensitive_change = describe_audit(entry, label)
        high_risk = action in RISKY_ACTIONS or sensitive_change
        sink = "security_log" if high_risk or action in AUTOMOD_ACTIONS else "audit"
        kind = "audit-risk" if high_risk else "audit"
        added = await self.record(guild, details, key=f"audit:{entry.id}", kind=kind,
                                  sink=sink,
                                  at=entry.created_at, immediate=False)
        now = datetime.now(timezone.utc)
        if added and high_risk and entry.created_at >= now - timedelta(seconds=60):
            await self.burst(guild, kind, 4, "60초 내 주요 관리 변경 4건 이상", now)
        if immediate:
            await self.flush(guild)

    async def catch_up(self, guild):
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return
        cursor = await self.store.get(guild.id, "audit:cursor")
        after = discord.Object(id=cursor) if cursor else datetime.now(timezone.utc) - timedelta(minutes=15)
        latest = cursor
        async for entry in guild.audit_logs(after=after, oldest_first=True, limit=100):
            await self.audit(entry, immediate=False)
            latest = entry.id
        if latest != cursor:
            await self.store.set(guild.id, "audit:cursor", latest)

    async def findings(self, guild):
        return await inspect_layout(guild, self.store) + await inspect_automod(guild, self.store)

    async def check(self, guild):
        findings = await self.findings(guild)
        previous = await self.store.get(guild.id, "ops:drift")
        if findings != previous:
            added = [finding for finding in findings if finding not in (previous or ())]
            resolved = [finding for finding in (previous or ()) if finding not in findings]
            if added or resolved:
                lines = [f"**설정 점검** · 현재 {len(findings)}건 · 신규 {len(added)}건 · 해소 {len(resolved)}건"]
                lines.extend(f"신규 · {finding}" for finding in added[:4])
                lines.extend(f"해소 · {finding}" for finding in resolved[:2])
                await self.record(guild, "\n".join(lines), sink="security_log", immediate=False)
            await self.store.set(guild.id, "ops:drift", findings)
        return findings
