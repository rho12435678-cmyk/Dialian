"""Render the structured Discord audit fields used in staff logs."""
from datetime import datetime

import discord

PERMISSIONS = {
    "administrator": "관리자",
    "manage_guild": "서버 관리",
    "manage_roles": "역할 관리",
    "manage_channels": "채널 관리",
    "manage_webhooks": "웹훅 관리",
    "view_audit_log": "감사 로그 보기",
    "kick_members": "멤버 추방",
    "ban_members": "멤버 차단",
    "moderate_members": "멤버 타임아웃",
    "manage_messages": "메시지 관리",
    "manage_threads": "스레드 관리",
    "mention_everyone": "전체 멘션",
    "read_messages": "채널 보기",
    "send_messages": "메시지 보내기",
    "send_messages_in_threads": "스레드에 메시지 보내기",
    "use_application_commands": "앱 명령어 사용",
    "connect": "음성 채널 연결",
    "speak": "음성 채널 말하기",
    "read_message_history": "메시지 기록 읽기",
}
FIELDS = {
    "name": "이름", "nick": "별명", "type": "유형", "position": "위치",
    "parent_id": "카테고리", "channel": "연결 채널", "owner": "소유자",
    "verification_level": "서버 인증 수준", "explicit_content_filter": "미디어 필터",
    "default_notifications": "기본 알림", "rules_channel": "규칙 채널",
    "public_updates_channel": "커뮤니티 업데이트 채널", "system_channel": "시스템 채널",
    "slowmode_delay": "슬로우모드(초)", "default_thread_slowmode_delay": "스레드 슬로우모드(초)",
    "nsfw": "연령 제한", "archived": "보관됨", "locked": "잠금", "enabled": "활성화",
    "timed_out_until": "타임아웃 종료", "mute": "서버 음소거", "deaf": "서버 헤드셋 차단",
    "mentionable": "역할 멘션 허용", "hoist": "멤버 목록에 역할 표시",
    "max_uses": "최대 사용 횟수(0=무제한)", "max_age": "유효 기간(초, 0=무제한)",
    "temporary": "임시 멤버십", "user_limit": "음성 인원 제한(0=무제한)",
    "trigger_type": "AutoMod 감지 유형", "event_type": "AutoMod 이벤트 유형",
}
PRIVATE_FIELDS = {
    "topic": "채널 주제", "actions": "AutoMod 조치", "trigger": "AutoMod 감지 조건",
    "exempt_roles": "AutoMod 제외 역할", "exempt_channels": "AutoMod 제외 채널",
}
MISSING = object()


def safe_name(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(" ".join(str(value).split())[:80]))


def identity(entity, *, identifier=None, name=None):
    identifier = identifier or getattr(entity, "id", None)
    name = name or getattr(entity, "display_name", None) or getattr(entity, "name", None)
    if name and identifier:
        return f"{safe_name(name)} (ID `{identifier}`)"
    if identifier:
        return f"ID `{identifier}`"
    return safe_name(name) if name else "Discord 기록 없음"


def _value(value):
    if value is MISSING:
        return "기록 없음"
    if value is None:
        return "설정 없음"
    if isinstance(value, bool):
        return "켜짐" if value else "꺼짐"
    if isinstance(value, datetime):
        return discord.utils.format_dt(value, "F")
    if hasattr(value, "id"):
        return identity(value)
    if isinstance(value, (str, int)):
        return safe_name(value)
    return safe_name(getattr(value, "name", "세부 정보 없음"))


def _short_list(items, limit=5):
    suffix = f" 외 {len(items) - limit}개" if len(items) > limit else ""
    return ", ".join(items[:limit]) + suffix


def _permission_names(flags):
    ordered = [flag for flag in PERMISSIONS if flag in flags] + sorted(flags - PERMISSIONS.keys())
    return _short_list([PERMISSIONS.get(flag, flag) for flag in ordered])


def _permission_change(before, after, *, gained, lost):
    old = {name for name, enabled in before if enabled}
    new = {name for name, enabled in after if enabled}
    return [f"{label}: {_permission_names(flags)}" for label, flags in
            ((gained, new - old), (lost, old - new)) if flags]


def _permission_lines(before, after):
    lines = []
    for key, enabled, removed in (
        ("permissions", "역할 권한 추가", "역할 권한 해제"),
        ("allow", "채널 허용 추가", "채널 허용 해제"),
        ("deny", "채널 거부 추가", "채널 거부 해제"),
    ):
        old, new = getattr(before, key, None), getattr(after, key, None)
        if isinstance(old, discord.Permissions) or isinstance(new, discord.Permissions):
            lines.extend(_permission_change(old or discord.Permissions.none(), new or discord.Permissions.none(),
                                            gained=enabled, lost=removed))
    return lines


def _overwrites(before, after):
    old = {target.id: (target, overwrite) for target, overwrite in getattr(before, "overwrites", ()) or ()}
    new = {target.id: (target, overwrite) for target, overwrite in getattr(after, "overwrites", ()) or ()}
    lines = []
    empty = (None, discord.PermissionOverwrite())
    for target_id in dict.fromkeys((*old, *new)):
        target = (new.get(target_id) or old[target_id])[0]
        old_allow, old_deny = old.get(target_id, empty)[1].pair()
        new_allow, new_deny = new.get(target_id, empty)[1].pair()
        changes = _permission_change(old_allow, new_allow, gained="허용 추가", lost="허용 해제")
        changes += _permission_change(old_deny, new_deny, gained="거부 추가", lost="거부 해제")
        if changes:
            lines.append(f"{identity(target)} · {' / '.join(changes)}")
    return lines


def _command_permissions(before, after):
    old = {rule.id: rule for rule in getattr(before, "app_command_permissions", ())}
    new = {rule.id: rule for rule in getattr(after, "app_command_permissions", ())}
    labels = {None: "기본값", True: "허용", False: "거부"}
    for target_id in dict.fromkeys((*old, *new)):
        old_rule, new_rule = old.get(target_id), new.get(target_id)
        previous, current = (getattr(rule, "permission", None) for rule in (old_rule, new_rule))
        if previous != current:
            target = (new_rule or old_rule).target
            yield f"명령어 권한 · {identity(target, identifier=target_id)} · {labels[previous]} → {labels[current]}"


def _audit_context(entry, before, after):
    extra = getattr(entry, "extra", None)
    action = entry.action.name
    channel = getattr(extra, "channel", None)
    lines = []
    if action.startswith("overwrite_") and extra is not None:
        lines.append(f"권한 적용 대상 · {identity(extra)}")
    if action == "app_command_permission_update" and extra is not None:
        lines.append(f"앱 · {identity(extra)}")
    if channel is not None:
        lines.append(f"관련 채널 · {identity(channel)}")
    if isinstance(count := getattr(extra, "count", None), int):
        lines.append(f"영향 건수 · {count}건")
    if isinstance(message_id := getattr(extra, "message_id", None), int):
        lines.append(f"메시지 ID `{message_id}`")
    if action == "member_prune" and extra is not None:
        lines.append(f"미활동 기준 {extra.delete_member_days}일 · 정리 {extra.members_removed}명")
    if rule := getattr(extra, "automod_rule_name", None):
        lines.append(f"감지 규칙 · {safe_name(rule)} · {_value(getattr(extra, 'automod_rule_trigger_type', None))}")
    for label, diff in (("제거된 역할", before), ("부여된 역할", after)):
        roles = getattr(diff, "roles", None)
        if roles:
            lines.append(f"{label} · {_short_list([identity(role) for role in roles], limit=3)}")
    return lines


def _changed_fields(guild, before, after):
    for key, label in FIELDS.items():
        old, new = getattr(before, key, MISSING), getattr(after, key, MISSING)
        if old == new:
            continue
        if key == "parent_id":
            old, new = (_category(guild, value) for value in (old, new))
        yield f"{label} · {_value(old)} → {_value(new)}"
    for key, label in PRIVATE_FIELDS.items():
        if getattr(before, key, MISSING) != getattr(after, key, MISSING):
            yield f"{label} 변경"


def _category(guild, value):
    if isinstance(value, int) or isinstance(value, str) and value.isdecimal():
        return guild.get_channel(int(value)) or discord.Object(id=value)
    return value


def describe_audit(entry, label):
    guild = entry.guild
    actor = getattr(entry, "user", None) or (guild.get_member(entry.user_id) if entry.user_id else None)
    try:
        before, after = getattr(entry, "before", None), getattr(entry, "after", None)
        target = entry.target
        parse_error = False
    except (AttributeError, KeyError, TypeError, ValueError):
        before = after = target = None
        parse_error = True
    # Discord may resolve an invite target to a raw code. Only its channel/settings are useful here.
    target_name = getattr(before, "name", None) or getattr(after, "name", None)
    target_id = getattr(target, "id", None) or getattr(entry, "_target_id", None)
    header = f"**{label}** · 감사 ID `{entry.id}`\n실행자 · {identity(actor, identifier=entry.user_id)}"
    if target_id or target_name or getattr(target, "name", None):
        header += f"\n대상 · {identity(target, identifier=target_id, name=target_name)}"
    permission_lines = _permission_lines(before, after) + _overwrites(before, after)
    permission_lines += list(_command_permissions(before, after))
    details = _audit_context(entry, before, after) + permission_lines + list(_changed_fields(guild, before, after))
    if parse_error:
        details.append("Discord가 보낸 세부 변경값을 해석하지 못했습니다. 원본 감사 로그에서 확인하세요.")
    elif not details:
        details.append("표시할 세부 정보가 Discord 감사 기록에 없습니다.")
    body = header
    for line in details:
        if len(body) + len(line) > 760:
            body += "\n… 추가 항목은 Discord 감사 로그에서 확인"
            break
        body += f"\n{line}"
    moderation = any(getattr(before, key, MISSING) != getattr(after, key, MISSING)
                     for key in ("timed_out_until", "mute", "deaf"))
    privileged_roles = any(
        isinstance(role, discord.Role) and any(getattr(role.permissions, flag) for flag in (
            "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
            "kick_members", "ban_members", "moderate_members",
        ))
        for diff in (before, after) for role in getattr(diff, "roles", ()) or ()
    )
    return body, bool(permission_lines) or moderation or privileged_roles
