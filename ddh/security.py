"""Discord-native safeguards for the DDH community.

Only DDH-prefixed AutoMod rules are created or edited. Unrelated moderator
rules are preserved. All operations are repeatable after a partial failure.
"""
import logging

import discord

from ddh.server import UserError

log = logging.getLogger(__name__)

RULE_INVITES = "DDH | 외부 서버 초대 차단"
RULE_SPAM = "DDH | 스팸 메시지 차단"
RULE_MENTIONS = "DDH | 멘션 도배 방지"


def specifications():
    """Rules kept separate from the network operation for review and testing."""
    return (
        (
            RULE_INVITES,
            discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=(
                    "*discord.gg/*", "*discord.com/invite/*",
                    "*discord.com/invites/*", "*discordapp.com/invite/*",
                ),
            ),
            True,  # Moderators can post approved partner announcements.
        ),
        (
            RULE_SPAM,
            discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam),
            False,
        ),
        (
            RULE_MENTIONS,
            discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.mention_spam,
                mention_limit=5,
                mention_raid_protection=True,
            ),
            False,
        ),
    )


def _diagnostic(stage, error):
    """Present safe, actionable failures to the server owner only."""
    if isinstance(error, UserError):
        return error
    if isinstance(error, discord.Forbidden):
        return UserError(
            f"보안 설정 실패 · {stage}: Discord 권한 부족(HTTP 403). "
            "Dialian 봇에 서버 관리(Manage Server) 권한이 있는지 확인하세요."
        )
    if isinstance(error, discord.HTTPException):
        return UserError(
            f"보안 설정 실패 · {stage}: Discord API 오류 "
            f"(HTTP {error.status}, 코드 {error.code}). "
            "서버 설정 → AutoMod 규칙 수와 봇 권한을 확인해 주세요."
        )
    return UserError(
        f"보안 설정 실패 · {stage}: 내부 처리 오류 "
        f"({type(error).__name__}). 봇 서비스 로그를 확인해 주세요."
    )


def _blocks(rule):
    return getattr(rule, "enabled", False) and any(
        getattr(action, "type", None) == discord.AutoModRuleActionType.block_message
        for action in getattr(rule, "actions", ())
    )


async def configure_security(guild, *, staff_role, audit_channel, admin_role=None):
    """Apply Discord-native security with precise stage diagnostics.

    Avoid claiming success if an existing non-DDH AutoMod rule is present but
    disabled. Never delete or overwrite moderation rules created by others.
    """
    if not guild.me.guild_permissions.manage_guild:
        raise UserError(
            "보안 설정 실패 · 서버 권한: Dialian 봇의 '서버 관리' 권한이 없습니다."
        )

    try:
        await guild.edit(
            verification_level=max(
                getattr(guild, "verification_level", discord.VerificationLevel.medium),
                discord.VerificationLevel.medium, key=lambda level: level.value,
            ),
            default_notifications=discord.NotificationLevel.only_mentions,
            explicit_content_filter=discord.ContentFilter.all_members,
            reason="DDH 신규 사용자·알림·콘텐츠 보안 설정",
        )
    except Exception as error:
        log.exception("DDH security failed while applying guild defaults")
        raise _diagnostic("서버 인증·알림·콘텐츠 필터", error) from error

    try:
        existing = await guild.fetch_automod_rules()
    except Exception as error:
        log.exception("DDH security failed fetching Discord AutoMod")
        raise _diagnostic("AutoMod 규칙 목록 조회", error) from error

    changes = []
    for name, trigger, staff_exempt in specifications():
        stage = f"AutoMod [{name}]"
        managed = next((rule for rule in existing if rule.name == name), None)
        matching = [
            rule for rule in existing
            if rule.trigger_type == trigger.type and rule is not managed
        ]
        if managed is None and trigger.type in (
            discord.AutoModRuleTriggerType.spam,
            discord.AutoModRuleTriggerType.mention_spam,
        ) and matching:
            if any(_blocks(rule) for rule in matching):
                log.info("DDH AutoMod: existing active %s rule preserved", trigger.type)
                changes.append(f"{name}: 기존 활성 규칙 유지")
                continue
            raise UserError(
                f"보안 설정 미완료 · {name}: 기존 Discord 규칙이 비활성화되어 있거나 "
                "메시지 차단 동작이 없습니다. 서버 설정 → AutoMod에서 기존 규칙을 "
                "켜고 메시지 차단을 활성화한 뒤 다시 실행하세요."
            )

        # Discord permits up to six keyword rules per guild.
        if (managed is None and trigger.type == discord.AutoModRuleTriggerType.keyword
                and sum(rule.trigger_type == trigger.type for rule in existing) >= 6):
            raise UserError(
                f"보안 설정 미완료 · {name}: Discord 키워드 AutoMod 규칙 한도 "
                "(6개)에 도달했습니다. 기존 규칙을 직접 확인해 주세요."
            )

        actions = [
            discord.AutoModRuleAction(
                custom_message="DDH에서는 외부 서버 초대 광고, 도배 및 과도한 멘션을 제한합니다."
            ),
            discord.AutoModRuleAction(channel_id=audit_channel.id),
        ]
        exemptions = [role for role in (staff_role, admin_role) if role] if staff_exempt else []
        try:
            if managed:
                await managed.edit(
                    trigger=trigger, actions=actions, enabled=True,
                    exempt_roles=exemptions, exempt_channels=[],
                    reason="DDH 보안 정책 동기화",
                )
                changes.append(f"{name}: 갱신")
            else:
                rule = await guild.create_automod_rule(
                    name=name,
                    event_type=discord.AutoModRuleEventType.message_send,
                    trigger=trigger, actions=actions, enabled=True,
                    exempt_roles=exemptions, reason="DDH 보안 정책 설치",
                )
                existing.append(rule)
                changes.append(f"{name}: 생성")
        except Exception as error:
            log.exception("DDH security failed configuring automod %s", name)
            raise _diagnostic(stage, error) from error

    try:
        confirmed = await guild.fetch_automod_rules()
    except Exception as error:
        log.exception("DDH security failed verifying AutoMod")
        raise _diagnostic("AutoMod 최종 검증", error) from error

    for name, trigger, _ in specifications():
        matching = [
            rule for rule in confirmed
            if rule.trigger_type == trigger.type
            and (rule.name == name or trigger.type in (
                discord.AutoModRuleTriggerType.spam,
                discord.AutoModRuleTriggerType.mention_spam,
            ))
        ]
        if not any(_blocks(rule) for rule in matching):
            raise UserError(
                f"보안 설정 미완료 · {name}: 규칙이 서버에 활성 상태로 확인되지 않았습니다. "
                "Discord 서버 설정 → AutoMod를 확인해 주세요."
            )
    return changes
