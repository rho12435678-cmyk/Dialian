"""Owner-requested cosmetic migration. Each explicit change is applied once."""
import discord

from ddh.blueprint import CHANNELS, overwrites
from ddh.server import UserError, forum_permissions, forum_tags, normalize_name, roles_for

LEGACY_MIGRATION_KEY = "layout:owner-cosmetics-2026-11"
MIGRATION_KEY = "layout:owner-cosmetics-2026-11-v2"
RESOURCE_NAMES = ("자료-resources", "개발자료", "자료", "resources")
QUESTION = next(spec for spec in CHANNELS if spec.key == "questions")
IDEAS = next(spec for spec in CHANNELS if spec.key == "ideas")
GUIDE = next(spec for spec in CHANNELS if spec.key == "guide")


def matching(channels, state, key, names, expected):
    aliases = {normalize_name(name) for name in names}
    saved = state.get(f"channel:{key}")
    other_channels = {value for entry, value in state.items()
                      if entry.startswith("channel:") and entry != f"channel:{key}" and isinstance(value, int)}
    return [channel for channel in channels if isinstance(channel, expected)
            and (channel.id == saved or (channel.id not in other_channels and normalize_name(channel.name) in aliases))]


def choose(channels, state, key):
    saved = next((channel for channel in channels if channel.id == state.get(f"channel:{key}")), None)
    if saved is not None:
        return saved
    if len(channels) > 1:
        raise UserError("같은 용도의 채널이 여러 개 있습니다. 적용할 채널 이름을 먼저 구분해 주세요.")
    return channels[0] if channels else None


async def cosmetic_layout(guild, store, *, apply=False):
    state = await store.settings(guild.id)
    if state.get(MIGRATION_KEY):
        return "이번 채널 정리는 이미 적용했습니다. 이후 꾸민 채널은 Discord에서 직접 관리하세요."
    channels = await guild.fetch_channels()
    guide = choose(matching(channels, state, "guide", (GUIDE.name, *GUIDE.aliases), discord.TextChannel), state, "guide")
    questions = matching(channels, state, "questions", (QUESTION.name, *QUESTION.aliases),
                         (discord.ForumChannel, discord.TextChannel))
    ideas = matching(channels, state, "ideas", (IDEAS.name,), (discord.TextChannel, discord.ForumChannel))
    staff_id = state.get("category:ops", 0)
    archived = [channel for channel in channels if isinstance(channel, (discord.ForumChannel, discord.TextChannel))
                and channel.category_id == staff_id]
    resources = matching(channels, state, "resources", RESOURCE_NAMES,
                         (discord.TextChannel, discord.ForumChannel))
    archived_resources = matching(archived, state, "resources",
                                  tuple(f"보관-{name}" for name in RESOURCE_NAMES),
                                  (discord.TextChannel, discord.ForumChannel))
    delete = {channel.id: channel for channel in (*resources, *archived_resources)}
    question = choose([c for c in questions if isinstance(c, discord.ForumChannel)], state, "questions")
    idea = choose([c for c in ideas if isinstance(c, discord.ForumChannel)], state, "ideas")
    restored_idea = None
    if idea is None and state.get(LEGACY_MIGRATION_KEY):
        restored_idea = choose(
            matching(archived, state, "ideas", (f"보관-{IDEAS.name}",), discord.ForumChannel),
            state, "ideas",
        )
    retire = {c.id: c for c in (*questions, *ideas) if isinstance(c, discord.TextChannel)}
    convert_guide = not state.get(LEGACY_MIGRATION_KEY) and (guide is None or guide.type != discord.ChannelType.news)
    summary = (
        ("가이드 공지 채널 전환\n" if convert_guide else "")
        + f"질문 포럼 · {question.mention if question else '새 포럼 생성'}\n"
        + f"아이디어 포럼 · {(idea or restored_idea).mention if idea or restored_idea else '새 포럼 생성'}\n"
        + f"Staff로 이동할 이전 채널 {len(retire)}개 · "
        + (", ".join(c.mention for c in retire.values()) or "없음")
        + f"\n영구 삭제할 자료 채널 {len(delete)}개 · "
        + (", ".join(c.mention for c in delete.values()) or "없음")
    )
    if not apply:
        return summary + "\n`/ddh layout apply:True`를 실행하면 자료 채널의 글과 첨부도 함께 삭제합니다."
    if convert_guide and guide is None:
        raise UserError("기존 가이드 채널을 찾지 못했습니다. 초기 구성은 /ddh setup을 확인해 주세요.")
    if convert_guide and "NEWS" not in guild.features:
        raise UserError("가이드를 공지 채널로 바꾸려면 Discord 커뮤니티 기능을 활성화해 주세요.")
    staff = guild.get_channel(staff_id)
    community = guild.get_channel(state.get("category:community", 0))
    if retire and not isinstance(staff, discord.CategoryChannel):
        raise UserError("이전 기록을 보존할 Staff 카테고리 연결이 필요합니다.")
    if (question is None or idea is None) and not isinstance(community, discord.CategoryChannel):
        raise UserError("포럼을 배치할 커뮤니티 카테고리 연결이 필요합니다.")
    if retire:
        incoming = sum(c.category_id != staff.id for c in retire.values())
        departing = sum(c.category_id == staff.id for c in delete.values()) + int(restored_idea is not None)
        if len(staff.channels) + incoming - departing > 50:
            raise UserError("Staff 카테고리에 이전 기록을 옮길 공간을 확보해 주세요.")
    role_keys = ("staff", "admin", "member") if question is None or idea is None else ("staff", "admin")
    roles = await roles_for(guild, store, keys=role_keys) if retire or question is None or idea is None else {}
    if convert_guide:
        await guide.edit(type=discord.ChannelType.news, reason="DDH 소유자 요청: 가이드 공지 채널 전환")
    if guide is not None:
        await store.set(guild.id, "channel:guide", guide.id)
    if question is None:
        question = await guild.create_forum(
            QUESTION.name, category=community, topic=QUESTION.topic, available_tags=forum_tags(QUESTION),
            overwrites=forum_permissions(guild, roles, QUESTION.policy), reason="DDH 소유자 요청: 질문 포럼")
    await store.set(guild.id, "channel:questions", question.id)
    if restored_idea is not None:
        idea = await restored_idea.edit(
            name=IDEAS.name, category=community, overwrites=forum_permissions(guild, roles, IDEAS.policy),
            reason="DDH 소유자 요청: 기존 아이디어 포럼 복원")
    elif idea is None:
        idea = await guild.create_forum(
            IDEAS.name, category=community, topic=IDEAS.topic, available_tags=forum_tags(IDEAS),
            overwrites=forum_permissions(guild, roles, IDEAS.policy), reason="DDH 소유자 요청: 아이디어 포럼")
    await store.set(guild.id, "channel:ideas", idea.id)
    for channel in delete.values():
        try:
            await channel.delete(reason="DDH 소유자 요청: 자료 채널 영구 삭제")
        except discord.NotFound:
            pass
    await store.set(guild.id, "channel:resources", None)
    for channel in retire.values():
        if channel.category_id == staff.id and channel.name.startswith("보관-"):
            continue
        await channel.edit(name=channel.name if channel.name.startswith("보관-") else f"보관-{channel.name}"[:100],
                           category=staff, overwrites=overwrites(guild, roles, "log"),
                           reason="DDH 소유자 요청: 이전 텍스트 채널 기록 보존")
    await store.set(guild.id, MIGRATION_KEY, True)
    return f"질문·아이디어 포럼 정리를 완료했습니다. 자료 채널 {len(delete)}개를 삭제했습니다."
