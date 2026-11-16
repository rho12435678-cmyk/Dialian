"""Durable operational events and read-only policy checks for DDH."""
import asyncio
import logging
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import discord

from ddh.backups import latest_backup, verify_snapshot
from ddh.blueprint import CATEGORIES, CHANNELS, ROLES
from ddh.security import rule_trigger_type, specifications
from ddh.server import REQUIRED_PERMISSIONS, unsafe_role_permissions

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



def person(user):
    if user is None:
        return "확인되지 않은 사용자"
    name = safe_name(getattr(user, "display_name", None) or getattr(user, "name", None) or "이름 확인 불가")
    return f"{name} (<@{user.id}>)" if getattr(user, "id", None) else name


PERMISSION_LABELS = {
    "administrator": "관리자", "manage_guild": "서버 관리", "manage_roles": "역할 관리",
    "manage_channels": "채널 관리", "manage_messages": "메시지 관리", "manage_threads": "스레드 관리",
    "manage_webhooks": "웹훅 관리", "view_audit_log": "감사 로그 보기", "kick_members": "멤버 추방",
    "ban_members": "멤버 차단", "moderate_members": "멤버 타임아웃", "view_channel": "채널 보기",
    "send_messages": "메시지 보내기", "send_messages_in_threads": "스레드 메시지 보내기",
    "attach_files": "파일 첨부", "embed_links": "링크 임베드", "read_message_history": "이전 메시지 보기",
    "mention_everyone": "전체·역할 멘션", "connect": "음성 연결", "speak": "음성 발언",
    "create_public_threads": "공개 스레드 생성", "create_private_threads": "비공개 스레드 생성",
    "mute_members": "서버 음소거", "deafen_members": "서버 헤드셋 음소거", "move_members": "음성 이동",
}


def change_value(value):
    if value is None:
        return "없음"
    if isinstance(value, bool):
        return "켜짐" if value else "꺼짐"
    if isinstance(value, datetime):
        return f"<t:{int(value.timestamp())}:f>"
    return safe_name(value)

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
        elif key not in ("staff", "admin") and unsafe_role_permissions(role):
            findings.append(f"자동 지급 역할의 관리 권한 확인 · {role.name}")
    if unsafe_role_permissions(guild.default_role):
        findings.append("@everyone 관리 권한 확인")
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
    if any(role and role >= guild.me.top_role for key, role in roles.items() if key not in ("staff", "admin")):
        findings.append("봇 역할 순서 확인 · 인증·신청 지급 대상 역할보다 Dialian을 위로 이동")
    for spec in CATEGORIES:
        channel = guild.get_channel(state.get(f"category:{spec.key}", 0))
        if not isinstance(channel, discord.CategoryChannel):
            findings.append(f"카테고리 누락 · {spec.name}")
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
        if spec.policy == "readonly":
            for key in ("member", "developer", "partner"):
                role = roles.get(key)
                if role and channel.permissions_for(role).send_messages:
                    findings.append(f"공지 채널 일반 역할 게시 허용 확인 · {channel.name} · {role.name}")
        # Check confidentiality, not equality with the initial template.
        if spec.policy in ("private", "log"):
            permitted = {state.get("role:staff"), state.get("role:admin"), guild.me.id}
            if not private_channel(channel, guild, permitted):
                findings.append(f"채널 권한 불일치 · {channel.name} · 비공개 접근 범위 확인")
    if state.get("visibility:verification_only"):
        verification_id = state.get("channel:verification")
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue
            public = channel.permissions_for(guild.default_role).view_channel
            trusted = {state.get("role:member"), state.get("role:staff"),
                       state.get("role:admin"), guild.me.top_role.id}
            if channel.id != verification_id:
                for role in guild.roles:
                    if role.id in trusted or role.id == guild.default_role.id:
                        continue
                    if channel.permissions_for(role).view_channel:
                        findings.append(f"미인증 추가 역할 접근 확인 · {channel.name} · {role.name}")
            if channel.id != verification_id and public:
                findings.append(f"미인증자 공개 채널 확인 · {channel.name}")
            elif channel.id == verification_id and not public:
                findings.append("인증 채널 접근 차단 확인")
    for ticket in await store.tickets(guild.id, "open"):
        channel = guild.get_channel(ticket["channel_id"])
        permitted = {state.get("role:staff"), state.get("role:admin"), guild.me.id, ticket["owner_id"], ticket.get("assigned_id")}
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

    async def record(self, guild, body, *, key=None, kind="operation", sink="audit", at=None, immediate=True, reference=None):
        if guild.id != self.bot.settings.guild_id:
            return False
        at = at or datetime.now(timezone.utc)
        added = await self.store.record_event(
            guild.id, key or uuid4().hex, sink, kind,
            json.dumps({"text": body[:3500], "reference": reference}, ensure_ascii=False), at.isoformat())
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
                for row in rows:
                    try:
                        payload = json.loads(row['body'])
                    except (ValueError, TypeError):
                        payload = {"text": row['body']}  # queued events from older versions
                    if not isinstance(payload, dict):
                        payload = {"text": row['body']}
                    security = sink == "security_log"
                    card = discord.Embed(
                        title="🛡️ 보안 · 관리자 확인 필요" if security else "📋 운영 활동 기록",
                        description=str(payload.get('text', ''))[:3500],
                        colour=0xE74C3C if security else 0x3498DB,
                        timestamp=datetime.fromisoformat(row['occurred_at']),
                    )
                    card.set_footer(text=("보안 설정·제재·이상 징후" if security else "일반 활동·문의·진행 내역")
                                    + (" · " + str(payload['reference'])[:200] if payload.get('reference') else ""))
                    try:
                        await channel.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        log.exception("DDH %s delivery deferred", sink)
                        break
                    await self.store.mark_delivered(guild.id, [row])

    async def membership(self, guild, user, *, joined):
        now = datetime.now(timezone.utc)
        joined_at = getattr(user, "joined_at", None)
        key = f"join:{user.id}:{joined_at.isoformat()}" if joined and joined_at else None
        label = "입장" if joined else "탈퇴·퇴장"
        added = await self.record(guild, f"**멤버 {label}**\n사용자: {person(user)}",
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
        actor = getattr(entry, "user", None) or guild.get_member(entry.user_id)
        target = entry.target
        target_name = safe_name(getattr(target, 'display_name', None) or getattr(target, 'name', None) or '이름 확인 불가')
        mention = getattr(target, 'mention', '')
        details = f"**{label}**\n**실행자:** {person(actor) if actor else f'<@{entry.user_id}>'}\n**대상:** {target_name} {mention}"
        extra = getattr(entry, 'extra', None)
        permission_target = getattr(extra, 'name', None)
        if permission_target:
            details += f"\n**권한 대상:** {safe_name(permission_target)}"
        count = getattr(extra, 'count', None)
        if count:
            details += f"\n**건수:** {count}"
        high_risk = action in RISKY_ACTIONS
        before = getattr(entry, "before", None)
        after = getattr(entry, "after", None)
        for field, title in (("name", "이름"), ("nick", "별명"), ("topic", "채널 설명"),
                             ("slowmode_delay", "슬로우모드(초)"), ("timed_out_until", "타임아웃 만료"),
                             ("nsfw", "성인 전용"), ("verification_level", "서버 인증 수준")):
            old, new = getattr(before, field, None), getattr(after, field, None)
            if old != new:
                details += f"\n**{title}:** {change_value(old)} → {change_value(new)}"
                if field in ('timed_out_until', 'verification_level'):
                    high_risk = True
        for field, title in (("permissions", "역할 권한"), ("allow", "채널 허용"), ("deny", "채널 거부")):
            old, new = getattr(before, field, None), getattr(after, field, None)
            if old is None and isinstance(new, discord.Permissions):
                old = discord.Permissions.none()
            if new is None and isinstance(old, discord.Permissions):
                new = discord.Permissions.none()
            if isinstance(old, discord.Permissions) and isinstance(new, discord.Permissions):
                added = [PERMISSION_LABELS.get(name, name) for name, enabled in new if enabled and not getattr(old, name)]
                removed = [PERMISSION_LABELS.get(name, name) for name, enabled in old if enabled and not getattr(new, name)]
                if added or removed:
                    details += f"\n**{title}**\n추가: {', '.join(added) or '없음'}\n제거: {', '.join(removed) or '없음'}"
                    high_risk = True
        if action == "member_role_update":
            for diff, title in ((before, "회수 역할"), (after, "지급 역할")):
                changed = getattr(diff, "roles", ())
                if changed:
                    details += "\n**" + title + ":** " + ", ".join(safe_name(role.name) for role in changed[:12])
                    high_risk |= any(unsafe_role_permissions(role) for role in changed)
        reason = getattr(entry, 'reason', None)
        if reason:
            details += "\n**사유:** " + discord.utils.escape_mentions(discord.utils.escape_markdown(str(reason)))[:400]
        kind = "audit-risk" if high_risk else "audit"
        added = await self.record(guild, details, key=f"audit:{entry.id}", kind=kind,
                                  sink="security_log" if high_risk else "audit",
                                  at=entry.created_at, immediate=False, reference=f"감사 기록 {entry.id}")
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
