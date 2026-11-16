"""Owner-requested community access repair. Never called from background jobs."""
import discord

from ddh.blueprint import CHANNELS, access, overwrites
from ddh.channels import fetch_supported_channels
from ddh.server import UserError, require_guild, roles_for, forum_tags, unsafe_role_permissions
from ddh.visibility import apply_visibility, find_verification_channel

FORUM_KEYS = ("hiring", "portfolio", "questions", "ideas", "pictures")
REASON = "DDH 소유자 요청: 개발자 포트폴리오 분리 및 활동 권한 정리"
CATEGORY_NAME = "🎨 개발자 포트폴리오"


def activity_policy(guild, roles, policy="member", *, voice=False):
    result = overwrites(guild, roles, policy, voice=voice)
    result[guild.default_role].view_channel = False
    if policy not in ("private", "log"):
        result[roles["member"]] = access(write=policy == "member", voice=voice)
        # Reading and commenting are open to verified members. On portfolios,
        # posting requires a developer role, but that role alone cannot bypass
        # the Member verification gate.
        if policy == "developer":
            result[roles["member"]].send_messages_in_threads = True
            result[roles["member"]].attach_files = True
            result[roles["member"]].embed_links = True
            result[roles["member"]].add_reactions = True
            for key in ("developer", *[key for key in roles if key.startswith("field:")]):
                result[roles[key]] = access()
                result[roles[key]].view_channel = None
    return result


async def repair_activity(bot, guild, *, apply=False):
    require_guild(guild, bot.settings)
    roles = await roles_for(guild, bot.store)
    state = await bot.store.settings(guild.id)
    protected_ids = {guild.default_role.id, roles["staff"].id, roles["admin"].id,
                     *[r.id for r in guild.me.roles]}
    repairs = []
    for key, role in roles.items():
        if key in ("staff", "admin"):
            continue
        unsafe = unsafe_role_permissions(role)
        if not unsafe:
            continue
        if role.id in protected_ids or role.managed or role >= guild.me.top_role:
            raise UserError(f"{role.name}: 자동 지급 역할의 연결/순서를 직접 확인해야 합니다. 아직 변경하지 않았습니다.")
        permissions = discord.Permissions(role.permissions.value)
        for permission in unsafe:
            setattr(permissions, permission, False)
        repairs.append((role, permissions))
    await find_verification_channel(guild, bot.store)
    specs = {spec.key: spec for spec in CHANNELS}
    channels = {}
    for key, spec in specs.items():
        channel = guild.get_channel(state.get(f"channel:{key}", 0))
        expected = discord.VoiceChannel if spec.voice else (discord.TextChannel, discord.ForumChannel)
        if not isinstance(channel, expected):
            raise UserError(f"{spec.name}: 기존 채널 연결을 먼저 확인해 주세요. 아무 채널도 변경하지 않았습니다.")
        channels[key] = channel
    if "COMMUNITY" not in guild.features:
        raise UserError("포럼을 사용하려면 Discord 커뮤니티 기능을 먼저 활성화해 주세요.")
    category = guild.get_channel(state.get("category:creators", 0))
    if category is None:
        matches = [c for c in guild.categories if c.name == CATEGORY_NAME]
        if len(matches) > 1:
            raise UserError("개발자 포트폴리오 카테고리가 중복되었습니다. 이름을 확인해 주세요.")
        category = matches[0] if matches else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        raise UserError("포트폴리오 카테고리 연결 정보가 잘못되었습니다.")
    new_forums = sum(not isinstance(channels[key], discord.ForumChannel) for key in FORUM_KEYS)
    if len(guild.channels) + new_forums + (category is None) > 500:
        raise UserError("새 포럼을 만들 서버 채널 공간이 부족합니다.")
    incoming = {}
    for key in FORUM_KEYS:
        current = channels[key]
        parent = category if key == "portfolio" else current.category
        if parent and (not isinstance(current, discord.ForumChannel) or current.category_id != parent.id):
            incoming[parent.id] = incoming.get(parent.id, 0) + 1
    for category_id, count in incoming.items():
        parent = guild.get_channel(category_id)
        if len(parent.channels) + count > 50:
            raise UserError(f"{parent.name}: 새 포럼을 배치할 공간이 부족합니다.")
    if not apply:
        return (
            "포트폴리오를 🎨 개발자 포트폴리오 카테고리로 분리합니다.\n"
            "포트폴리오: 개발자·분야 역할 게시, 인증된 멤버 열람·댓글\n"
            "구인·질문·아이디어·작품 공유: 인증된 멤버 모두 게시·댓글·파일 첨부\n"
            f"기존 텍스트에서 새 포럼 생성 {new_forums}개 (이전 글은 읽기 전용 채널로 보존)\n"
            f"일반 자동 지급 역할의 과도한 관리 권한 제거 {len(repairs)}개\n"
            "일반 채팅·음성 이용 권한과 공지 읽기 전용·Staff 비공개 권한도 정리합니다.\n"
            "미인증자는 인증 채널만 표시합니다. /ddh activity apply:True로 한 번 적용하세요."
        )
    if not guild.me.guild_permissions.manage_channels:
        raise UserError("봇에 채널 관리 권한이 필요합니다.")
    if repairs and not guild.me.guild_permissions.manage_roles:
        raise UserError("일반 역할의 과도한 권한 정리에 역할 관리 권한이 필요합니다. 아직 변경하지 않았습니다.")
    # Read before mutations; a failed REST request must not partially apply a plan.
    live_channels = await fetch_supported_channels(guild)
    for role, permissions in repairs:
        await role.edit(permissions=permissions, reason=REASON)
    # Gate existing channels before writing the final activity policies; Discord
    # gateway caches may lag behind REST edits during this multi-step command.
    await apply_visibility(bot, guild, apply=True)
    if category is None:
        category = await guild.create_category(
            CATEGORY_NAME, overwrites=activity_policy(guild, roles, "readonly"), reason=REASON,
        )
    await bot.store.set(guild.id, "category:creators", category.id)
    for key in FORUM_KEYS:
        channel = channels[key]
        spec = specs[key]
        policy = activity_policy(guild, roles, "developer" if key == "portfolio" else "member")
        if not isinstance(channel, discord.ForumChannel):
            # A forum cannot replace a text channel in place. Journal both IDs
            # and use a marker to recover a creation interrupted before saving.
            await bot.store.set(guild.id, f"activity:archive:{key}", channel.id)
            marker = f"{spec.topic}\n이전 대화 기록: <#{channel.id}>"
            existing = [c for c in live_channels
                        if isinstance(c, discord.ForumChannel) and c.topic == marker]
            if len(existing) > 1:
                raise UserError(f"{key}: 복구할 새 포럼이 중복되었습니다.")
            target_category = category if key == "portfolio" else channel.category
            forum = existing[0] if existing else await guild.create_forum(
                spec.name, category=target_category, topic=marker,
                overwrites=policy, available_tags=forum_tags(spec),
                slowmode_delay=0, default_thread_slowmode_delay=0, reason=REASON,
            )
            await bot.store.set(guild.id, f"channel:{key}", forum.id)
            channel = forum
        params = dict(overwrites=policy, slowmode_delay=0, default_thread_slowmode_delay=0, require_tag=False, reason=REASON)
        if key == "portfolio":
            params["category"] = category
        await channel.edit(**params)
        channels[key] = channel
        archive = guild.get_channel(await bot.store.get(guild.id, f"activity:archive:{key}", 0))
        if isinstance(archive, discord.TextChannel):
            await archive.edit(
                name=archive.name if archive.name.startswith("보관-") else f"보관-{archive.name}"[:100],
                overwrites=activity_policy(guild, roles, "readonly"), reason=REASON,
            )
    # No ticket channels or user-created channels are reset here. Only the tracked community channels requested by the owner.
    for key, channel in channels.items():
        if key in FORUM_KEYS:
            continue
        spec = specs[key]
        params = dict(overwrites=activity_policy(guild, roles, spec.policy, voice=spec.voice), reason=REASON)
        if key == "updates":
            params["overwrites"][guild.me].mention_everyone = True
        if not spec.voice and spec.policy == "member":
            params["slowmode_delay"] = 0
        await channel.edit(**params)
    await bot.store.set(guild.id, "activity:configured", True)
    return (
        "포트폴리오 분리 및 5개 포럼 게시·댓글·파일 권한 적용 완료.\n"
        "기존 텍스트 글은 보관 채널에 보존했습니다. 일반 채팅·음성/공지/Staff 접근도 정리했습니다.\n"
        f"일반 지급 역할의 과도한 관리 권한 {len(repairs)}개 정리. 운영자·관리자 역할은 유지했습니다.\n"
        "다음 재시작에서 이 설정을 자동으로 다시 적용하지 않습니다."
    )
