"""Declarative server layout and permission policy."""
from dataclasses import dataclass

import discord

FIELDS = {
    "gfx": "GFX", "ui": "UI", "clothing": "복장", "scripting": "스크립터",
    "vfx": "VFX", "building": "빌더", "modeling": "모델러", "animation": "애니메이터",
}
KINDS = {"partner": "파트너 문의", "feedback": "서버 피드백", "developer": "개발자 신청"}
ROLES = {
    "staff": ("DDH 운영진", 0xE67E22), "developer": ("개발자", 0x3498DB),
    "partner": ("파트너", 0x9B59B6), "member": ("멤버", 0x95A5A6),
    "update_alerts": ("업데이트 알림", 0x2ECC71),
    **{f"field:{key}": (label, 0xF1C40F) for key, label in FIELDS.items()},
}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    private: bool = False
    aliases: tuple[str, ...] = ()


CATEGORIES = (
    Category("info", "📌 안내", aliases=("Information",)),
    Category("community", "💬 커뮤니티", aliases=("Text Channels",)),
    Category("jobs", "🤝 구인·구직"), Category("portfolios", "🎨 포트폴리오"),
    Category("voice", "🔊 음성 채널", aliases=("Voice Channels",)),
    Category("support", "📨 문의"), Category("tickets", "🔒 문의 진행", True),
    Category("archive", "📁 문의 보관", True), Category("ops", "🛠 운영", True),
)


@dataclass(frozen=True)
class Channel:
    key: str
    name: str
    category: str
    policy: str = "member"
    voice: bool = False
    aliases: tuple[str, ...] = ()
    topic: str = ""


CHANNELS = (
    Channel("rules", "시작·규칙", "info", "readonly", aliases=("welcome-and-rules",)),
    Channel("announcements", "공지사항", "info", "readonly", aliases=("announcements",)),
    Channel("updates", "업데이트-공지", "info", "readonly",
            aliases=("업데이트 공지", "update-announcements", "updates"),
            topic="DDH 공식 업데이트 · 공지 알림 신청 버튼으로 새 버전 소식을 받아보세요."),
    Channel("guide", "이용안내", "info", "readonly"),
    Channel("resources", "개발자료", "community", aliases=("resources",)),
    Channel("general", "자유채팅", "community", aliases=("general",)),
    Channel("english", "english-chat", "community"),
    Channel("questions", "개발질문", "community"),
    Channel("showcase", "작품공유·피드백", "community"),
    Channel("meetings", "모임일정", "community", aliases=("meeting-plans",)),
    Channel("offtopic", "잡담", "community", aliases=("off-topic",)),
    Channel("hiring", "구인", "jobs", topic="분야·업무·예산/보상·일정·연락 방법을 명시하세요. 협의는 당사자 DM에서 진행합니다."),
    Channel("looking", "구직", "jobs", "developer", topic="개발자 역할 승인 후 분야·포트폴리오·가능 일정·연락 방법을 작성하세요."),
    *[Channel(f"portfolio:{key}", f"{key}-포트폴리오", "portfolios", f"field:{key}",
              topic=f"{label} 개발자 작품·역할·작업 범위·연락 방법. 문의와 거래는 당사자 DM으로 진행하세요.")
      for key, label in FIELDS.items()],
    Channel("lounge", "라운지", "voice", voice=True, aliases=("Lounge",)),
    Channel("workroom", "공동작업실", "voice", voice=True),
    Channel("meeting_voice", "미팅룸", "voice", voice=True, aliases=("Meeting Room",)),
    Channel("support_panel", "문의·신청", "support", "readonly"),
    Channel("partners", "파트너-소개", "support", "readonly"),
    Channel("staff_chat", "운영진-대화", "ops", "private"),
    Channel("audit", "운영-로그", "ops", "private"),
    Channel("staff_voice", "운영진-회의", "ops", "private", voice=True),
)


def role_permissions(key):
    if key == "staff":
        return discord.Permissions(manage_messages=True, moderate_members=True,
                                   mute_members=True, deafen_members=True, move_members=True)
    return discord.Permissions.none()


def everyone_permissions():
    return discord.Permissions(view_channel=True, read_message_history=True, use_application_commands=True)


def access(*, write=True, voice=False):
    return discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=write,
        attach_files=write, embed_links=write, add_reactions=write,
        create_public_threads=False, create_private_threads=False,
        send_messages_in_threads=write, mention_everyone=False,
        connect=voice, speak=voice, stream=voice, use_voice_activation=voice,
    )


def overwrites(guild, roles, policy="member", *, voice=False, owner=None, closed=False):
    private = policy == "private"
    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=not private, read_message_history=not private,
            send_messages=False, send_messages_in_threads=False, add_reactions=False,
            create_public_threads=False, create_private_threads=False,
            connect=False, speak=False, mention_everyone=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, attach_files=True, manage_channels=True, manage_messages=True,
            connect=True, speak=True,
        ),
        roles["staff"]: access(voice=voice),
    }
    writer = roles.get(policy)
    if writer:
        result[writer] = access(voice=voice)
    if owner is not None:
        result[owner] = access(write=not closed)
    return result


def layout_summary():
    lines = ["**Dial Dev Hub 서버 구성**", "역할: " + ", ".join(v[0] for v in ROLES.values())]
    for category in CATEGORIES:
        names = [c.name for c in CHANNELS if c.category == category.key]
        lines.append(f"• {category.name}: {', '.join(names) or '비공개 티켓 자동 생성'}")
    lines.append("\n적용 시 관리 대상 채널의 이름·권한·분류와 기본 역할 권한을 맞춥니다. "
                 "사진의 기본 채널은 이름으로 재사용하며 그 외 채널은 삭제하지 않습니다.")
    return "\n".join(lines)
