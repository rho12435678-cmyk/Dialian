"""Discord-native safeguards for the DDH community.

Only DDH-prefixed AutoMod rules are created or edited. Unrelated moderator
rules are preserved. All operations are repeatable after a partial failure.
"""
import logging

import discord

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
                    "discord.gg/", "discord.com/invite/",
                    "discord.com/invites/", "discordapp.com/invite/",
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


async def configure_security(guild, *, staff_role, audit_channel):
    """Enable server protections and upsert DDH rules without deleting others.

    Requires Manage Server on the bot itself. Existing Discord spam / mention
    spam rules created by human moderators are left alone.
    """
    await guild.edit(
        verification_level=discord.VerificationLevel.medium,
        default_notifications=discord.NotificationLevel.only_mentions,
        explicit_content_filter=discord.ContentFilter.all_members,
        reason="DDH 신규 사용자·알림·콘텐츠 보안 설정",
    )
    try:
        existing = await guild.fetch_automod_rules()
    except discord.NotFound:
        existing = []

    changes = []
    for name, trigger, staff_exempt in specifications():
        managed = next((rule for rule in existing if rule.name == name), None)
        # Discord may only allow one native spam / mention rule. Do not take
        # over an existing moderator-created rule to make space.
        if managed is None and trigger.type in (
            discord.AutoModRuleTriggerType.spam,
            discord.AutoModRuleTriggerType.mention_spam,
        ):
            if any(rule.trigger_type == trigger.type for rule in existing):
                log.info("DDH AutoMod: keeping existing %s rule", trigger.type)
                changes.append(f"{name}: 기존 규칙 유지")
                continue

        actions = [
            discord.AutoModRuleAction(custom_message=(
                "DDH에서는 외부 서버 초대 광고, 도배 및 과도한 멘션을 제한합니다."
            )),
            discord.AutoModRuleAction(channel_id=audit_channel.id),
        ]
        exemptions = [staff_role] if staff_exempt else []
        if managed:
            await managed.edit(
                trigger=trigger,
                actions=actions,
                enabled=True,
                exempt_roles=exemptions,
                exempt_channels=[],
                reason="DDH 보안 정책 동기화",
            )
            changes.append(f"{name}: 갱신")
        else:
            rule = await guild.create_automod_rule(
                name=name,
                event_type=discord.AutoModRuleEventType.message_send,
                trigger=trigger,
                actions=actions,
                enabled=True,
                exempt_roles=exemptions,
                reason="DDH 보안 정책 설치",
            )
            existing.append(rule)
            changes.append(f"{name}: 생성")
    return changes
