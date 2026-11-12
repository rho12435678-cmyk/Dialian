"""Declarative server layout and permission policy."""
from dataclasses import dataclass

import discord

FIELDS = {
    "gfx": "GFX", "ui": "UI", "clothing": "복장", "scripting": "스크립터",
    "vfx": "VFX", "building": "빌더", "modeling": "모델러", "animation": "애니메이터",
}
KINDS = {"partner": "파트너 문의", "feedback": "서버 피드백", "developer": "개발자 신청"}
LAYOUT_VERSION = "2026-11-minimal-bilingual-v1"

# Preserve field-specific verification and ticket approvals, but expose just
# one portfolio board with field tags to reduce the visible channel list.
FIELD_ROLE_NAMES = {
    "gfx": "GFX・그래픽",
    "ui": "UI・인터페이스",
    "clothing": "복장・Clothing",
    "scripting": "스크립터・Scripter",
    "vfx": "VFX・이펙트",
    "building": "빌더・Builder",
    "modeling": "모델러・Modeler",
    "animation": "애니메이터・Animator",
}
ROLES = {
    "staff": ("운영진・Staff", 0xE67E22),
    "developer": ("개발자・Developer", 0x3498DB),
    "partner": ("파트너・Partner", 0x9B59B6),
    "member": ("멤버・Member", 0x95A5A6),
    "update_alerts": ("알림・Updates", 0x2ECC71),
    **{f"field:{key}": (FIELD_ROLE_NAMES[key], 0xF1C40F) for key in FIELDS},
}

# Only used when the tracked role ID is unavailable (for old DDH installs).
ROLE_ALIASES = {
    "staff": ("DDH 운영진",),
    "developer": ("개발자",),
    "partner": ("파트너",),
    "member": ("멤버",),
    "update_alerts": ("업데이트 알림",),
    **{f"field:{key}": (name,) for key, name in FIELDS.items()},
}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    private: bool = False
    aliases: tuple[str, ...] = ()


# Five public sections; ticket and staff areas remain private.
CATEGORIES = (
    Category("info", "📌 안내・Info", aliases=("📌 안내", "Information")),
    Category("support", "📨 문의・Support", aliases=("📨 문의",)),
    Category("community", "💬 커뮤니티・Chat", aliases=("💬 커뮤니티", "Text Channels")),
    Category("jobs", "💼 거래・Market", aliases=("🤝 구인·구직",)),
    Category("voice", "🔊 음성・Voice", aliases=("🔊 음성 채널", "Voice Channels")),
    Category("tickets", "🔐 티켓・Tickets", True, aliases=("🔒 문의 진행",)),
    Category("archive", "📁 보관・Archive", True, aliases=("📁 문의 보관",)),
    Category("ops", "🔒 운영・Staff", True, aliases=("🛠 운영",)),
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
    forum: bool = False
    news: bool = False


CHANNELS = (
    Channel("rules", "규칙-rules", "info", "readonly",
            aliases=("시작·규칙", "welcome-and-rules")),
    Channel("announcements", "공지-news", "info", "readonly",
            aliases=("공지사항", "announcements"), news=True),
    Channel("updates", "업데이트-updates", "info", "readonly",
            aliases=("업데이트-공지", "업데이트 공지", "update-announcements", "updates"),
            topic="DDH 공식 업데이트와 알림 신청", news=True),
    Channel("guide", "가이드-guide", "info", "readonly",
            aliases=("가이드", "이용안내", "guide"),
            topic="DDH 소개 · 규칙 동의 · 개발자 등록 · 협업 안내"),

    # Announcement channels with permanent bot buttons, no regular user posts.
    Channel("support_panel", "문의-support", "support", "readonly",
            aliases=("문의·신청",), news=True),
    Channel("partners", "파트너-partners", "support", "readonly",
            aliases=("파트너-소개",), news=True),

    Channel("general", "채팅-chat", "community",
            aliases=("자유채팅", "general")),
    Channel("questions", "질문-help", "community",
            aliases=("개발질문",)),
    Channel("resources", "자료-resources", "community",
            aliases=("개발자료", "resources")),

    Channel("hiring", "구인-hiring", "jobs", forum=True,
            aliases=("구인",),
            topic="팀원 모집: 프로젝트 / 역할 / 예산·보상 / 기간 / 연락 방법"),
    Channel("commissions", "의뢰-commissions", "jobs", forum=True,
            aliases=("제작의뢰",),
            topic="작업 의뢰: 분야 / 요구사항 / 예산 / 납기 / 연락 방법. DDH는 결제와 거래를 중개하지 않습니다."),
    Channel("portfolio", "포트폴리오-portfolio", "jobs", "developer", forum=True,
            aliases=("포트폴리오",),
            topic="개발자 전용 포트폴리오: 분야 태그 / 본인 작업 / 가격·의뢰 가능 여부 / 연락 방법. 회원은 댓글과 DM으로 문의 가능합니다."),

    Channel("lounge", "라운지-voice", "voice", voice=True, aliases=("라운지", "Lounge")),
    Channel("workroom", "작업-work", "voice", voice=True, aliases=("공동작업실",)),

    Channel("staff_chat", "운영진-chat", "ops", "private",
            aliases=("운영진-대화",)),
    Channel("audit", "로그-logs", "ops", "private",
            aliases=("운영-로그",)),
)

# Resources removed from the public layout are archived instead of deleted.
OBSOLETE_CHANNEL_KEYS = (
    "hiring", "commissions", "english", "showcase", "meetings", "offtopic", "looking",
    "meeting_voice", "staff_voice",
    *(f"portfolio:{key}" for key in FIELDS),
)
OBSOLETE_CHANNEL_NAMES = {
    "hiring": ("구인", "구인-기존글", "구인-hiring-기존글"),
    "commissions": ("제작의뢰", "제작의뢰-기존글", "의뢰-commissions-기존글"),
    "english": ("english-chat",),
    "showcase": ("작품공유·피드백",),
    "meetings": ("모임일정", "meeting-plans"),
    "offtopic": ("잡담", "off-topic"),
    "looking": ("구직",),
    "meeting_voice": ("미팅룸", "Meeting Room"),
    "staff_voice": ("운영진-회의",),
    **{f"portfolio:{key}": (f"{key}-포트폴리오",) for key in FIELDS},
}


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
