"""Durable operational events and read-only policy checks for DDH."""
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import discord

from ddh.backups import latest_backup, verify_snapshot
from ddh.blueprint import CATEGORIES, CHANNELS, ROLES, overwrites, role_permissions
from ddh.security import rule_trigger_type, specifications
from ddh.server import REQUIRED_PERMISSIONS, forum_permissions

log = logging.getLogger(__name__)

RISKY_ACTIONS = {
    "channel_delete", "role_delete", "overwrite_create", "overwrite_update",
    "overwrite_delete", "webhook_create", "webhook_update", "webhook_delete",
    "guild_update", "automod_rule_delete", "bot_add", "kick", "ban",
}
AUDIT_LABELS = {
    "guild_update": "서버 설정 변경", "channel_create": "채널 생성", "channel_update": "채널 변경",
    "channel_delete": "채널 삭제", "overwrite_create": "채널 권한 추가",
    "overwrite_update": "채널 권한 변경", "overwrite_delete": "채널 권한 삭제",
    "role_create": "역할 생성", "role_update": "역할 변경", "role_delete": "역할 삭제",
    "member_role_update": "멤버 역할 변경", "member_update": "멤버 관리 변경",
    "bot_add": "봇 추가", "kick": "추방", "ban": "차단", "unban": "차단 해제",
    "prune": "비활성 멤버 정리", "invite_create": "초대 생성", "invite_delete": "초대 삭제",
    "webhook_create": "웹훅 생성", "webhook_update": "웹훅 변경", "webhook_delete": "웹훅 삭제",
    "thread_create": "포럼 게시글 생성 감사 기록", "thread_update": "게시글 설정 변경", "thread_delete": "게시글 삭제",
    "automod_rule_create": "AutoMod 규칙 생성", "automod_rule_update": "AutoMod 규칙 변경",
    "automod_rule_delete": "AutoMod 규칙 삭제", "app_command_permission_update": "앱 명령 권한 변경",
    "message_delete": "메시지 삭제 감사 기록", "message_bulk_delete": "메시지 일괄 삭제 감사 기록",
}


def safe_name(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(" ".join(str(value).split())[:80]))


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
        elif key == "staff":
            # The owner-created operator role is intentionally not overwritten.
            # Still warn if it has powers that could bypass controlled workflows.
            if role.permissions.administrator or role.permissions.manage_roles:
                findings.append("운영자 역할 과도한 권한 확인 · Administrator/역할 관리")
            if not role.permissions.manage_messages:
                findings.append("운영자 역할 메시지 관리 권한 확인")
        elif role.permissions != role_permissions(key):
            findings.append(f"역할 권한 불일치 · {ROLES[key][0]}")
    orphaned = [
        role for role in guild.roles
        if role.name in ("운영진・Staff", "DDH 운영진")
        and role.id != getattr(roles["staff"], "id", None)
    ]
    if orphaned:
        findings.append(
            "사용 중단된 이전 운영진 역할 확인 · 멤버·비공개 채널 권한을 확인한 뒤 수동 삭제"
        )
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
        elif roles["staff"] and roles["admin"]:
            policy = "private" if spec.private else "readonly"
            if channel.overwrites != overwrites(guild, roles, policy):
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
        if roles["staff"] and roles["admin"] and (spec.policy not in roles or roles[spec.policy]):
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
            if trigger.type != discord.AutoModRuleTriggerType.keyword and rule_trigger_type(rule) == trigger.type]
        if not any(rule.enabled and rule_trigger_type(rule) == trigger.type
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
            for sink in ("security_log", "audit"):
                rows = await self.store.pending_events(guild.id, sink, limit=4)
                if not rows:
                    continue
                channel = guild.get_channel(state.get(f"channel:{sink}", 0))
                if not private_channel(channel, guild, permitted):
                    log.warning("DDH log sink %s unavailable or has unsafe overwrites", sink)
                    continue
                lines = [f"<t:{int(datetime.fromisoformat(row['occurred_at']).timestamp())}:f> · {row['body']}"
                         for row in rows]
                card = discord.Embed(title="DDH 보안" if sink == "security_log" else "DDH 운영 기록",
                                     description="\n".join(lines))
                try:
                    await channel.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    log.exception("DDH %s delivery deferred", sink)
                    continue
                await self.store.mark_delivered(guild.id, rows)

    async def membership(self, guild, user, *, joined):
        now = datetime.now(timezone.utc)
        joined_at = getattr(user, "joined_at", None)
        key = f"join:{user.id}:{joined_at.isoformat()}" if joined and joined_at else None
        label = "입장" if joined else "탈퇴·퇴장"
        added = await self.record(guild, f"{label} · {safe_name(user.name)} · ID `{user.id}`",
                                  key=key, kind="join" if joined else "leave", at=now, immediate=False)
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
        if guild.id != self.bot.settings.guild_id or entry.user_id == getattr(guild.me, "id", None):
            return
        action = entry.action.name
        label = AUDIT_LABELS.get(action)
        if label is None:
            return
        target_id = getattr(entry.target, "id", None)
        actor = str(entry.user_id) if entry.user_id else "확인 불가"
        details = f"{label} · 실행자 `{actor}` · 대상 `{target_id or '확인 불가'}` · 감사 ID `{entry.id}`"
        high_risk = action in RISKY_ACTIONS
        if action == "role_update":
            before = getattr(entry.before, "permissions", None)
            after = getattr(entry.after, "permissions", None)
            if before is not None and after is not None:
                details += f" · 권한 비트 `{before.value} → {after.value}`"
                high_risk = True
        kind = "audit-risk" if high_risk else "audit"
        added = await self.record(guild, details, key=f"audit:{entry.id}", kind=kind,
                                  sink="security_log" if high_risk else "audit",
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
        warnings = await inspect_layout(guild, self.store) + await inspect_automod(guild, self.store)
        warnings += await asyncio.to_thread(self.backup_warnings)
        return warnings

    def backup_warnings(self):
        source = self.bot.settings.database
        latest = latest_backup(source)
        if latest is None:
            return ["DB 백업 없음 · 봇이 백업을 만들었는지 점검하세요"]
        now = datetime.now(timezone.utc).timestamp()
        if now - latest.stat().st_mtime > 36 * 3600:
            return ["DB 백업 지연 · 최신 백업이 36시간 이상 경과"]
        try:
            verify_snapshot(latest)
        except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError):
            log.exception("DDH latest backup verification failed")
            return ["DB 백업 검증 실패 · 원본 백업과 디스크 상태를 점검하세요"]
        return []

    async def check(self, guild):
        findings = await self.findings(guild)
        previous = await self.store.get(guild.id, "ops:drift")
        if findings != previous:
            if findings or previous:
                summary = "설정 점검 · " + (" / ".join(findings[:10]) if findings else "권한 불일치 해소")
                await self.record(guild, summary, sink="security_log", immediate=False)
            await self.store.set(guild.id, "ops:drift", findings)
        return findings
