"""Declarative server layout and permission policy."""
from dataclasses import dataclass

import discord

FIELDS = {
    "gfx": "GFX", "ui": "UI", "clothing": "복장", "scripting": "스크립터",
    "vfx": "VFX", "building": "빌더", "modeling": "모델러", "animation": "애니메이터",
}
KINDS = {"partner": "파트너 문의", "feedback": "서버 피드백", "developer": "개발자 신청"}
LAYOUT_VERSION = "2026-11-owner-controlled-v4"

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
    "admin": ("관리자・Admin", 0xD35400),
    "developer": ("개발자・Developer", 0x3498DB),
    "partner": ("파트너・Partner", 0x9B59B6),
    "member": ("멤버・Member", 0x95A5A6),
    "external_bots": ("외부봇・Bots", 0x7289DA),
    "update_alerts": ("알림・Updates", 0x2ECC71),
    **{f"field:{key}": (FIELD_ROLE_NAMES[key], 0xF1C40F) for key in FIELDS},
}

# Only used when the tracked role ID is unavailable (for old DDH installs).
ROLE_ALIASES = {
    "staff": ("DDH 운영진",),
    "admin": ("관리자", "Admin"),
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


# Entry precedes Info on new servers. Private channels use their own overwrites.
CATEGORIES = (
    Category("entry", "🚪 입장・Entry"),
    Category("info", "📌 안내・Info", aliases=("📌 안내", "Information", "📌 안내 • Info")),
    Category("support", "📨 문의・Support", aliases=("📨 문의", "📨 문의 • Support")),
    Category("community", "💬 커뮤니티・Chat", aliases=("💬 커뮤니티", "Text Channels", "💬 커뮤니티 • Chat")),
    Category("jobs", "💼 거래・Market", aliases=("🤝 구인·구직", "💼 거래 • Market")),
    Category("voice", "🔊 음성・Voice", aliases=("🔊 음성 채널", "Voice Channels", "🔊 음성 • Voice")),
    Category("ops", "🔒 운영・Staff", True, aliases=("🛠 운영", "🔒 운영 • Staff")),
)


def category_policy(spec):
    if spec.key == "entry":
        return "verification"
    return "private" if spec.private else "readonly"


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
    Channel("verification", "인증-verify", "entry", "verification",
            topic="규칙을 읽고 버튼을 눌러 DDH 멤버 인증"),

    Channel("member_log", "입퇴장-join-leave", "entry", "log",
            topic="멤버의 입장·퇴장 기록"),

    # News channels are the first visible items in the Info category.
    Channel("announcements", "공지-news", "info", "readonly",
            aliases=("공지사항", "announcements"), news=True),
    Channel("updates", "업데이트-updates", "info", "readonly",
            aliases=("업데이트-공지", "업데이트 공지", "update-announcements", "updates"),
            topic="DDH 공식 업데이트와 알림 신청", news=True),
    Channel("rules", "규칙-rules", "info", "readonly",
            aliases=("시작·규칙", "welcome-and-rules")),
    Channel("guide", "가이드-guide", "info", "readonly",
            aliases=("가이드", "이용안내", "guide"),
            topic="DDH 소개 · 규칙 동의 · 개발자 등록 · 협업 안내", news=True),

    # Announcement channels with permanent bot buttons, no regular user posts.
    Channel("support_panel", "문의-support", "support", "readonly",
            aliases=("문의·신청",), news=True),
    Channel("partners", "파트너-partners", "support", "readonly",
            aliases=("파트너-소개",), news=True),

    Channel("general", "채팅-chat", "community",
            aliases=("자유채팅", "general")),
    Channel("commands", "명령어-commands", "community", "commands",
            topic="인증 멤버와 외부 봇의 명령어 사용 공간"),
    Channel("pictures", "사진-pics", "community", topic="Roblox 작업 이미지·스크린샷 공유"),
    Channel("questions", "질문-question", "community", forum=True,
            aliases=("개발질문", "질문-help"),
            topic="개발 질문: 환경 / 기대 동작 / 오류 / 시도한 방법. 토큰과 개인정보는 지워 주세요."),
    Channel("ideas", "아이디어-ideas", "community", forum=True,
            topic="게임과 커뮤니티 아이디어: 제안 / 기대 효과 / 필요한 작업"),

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
    Channel("audit", "로그-logs", "ops", "log", aliases=("운영-로그",)),
    Channel("security_log", "보안-security", "ops", "log",
            topic="AutoMod 및 권한·변경량 점검 알림"),
)

def role_permissions(key):
    if key == "admin":
        # Dedicated management role, but no dangerous global Administrator
        # bypass. The server owner must assign it manually.
        return discord.Permissions(
            manage_guild=True, manage_channels=True, manage_roles=True,
            manage_messages=True, manage_threads=True, view_audit_log=True,
            kick_members=True, ban_members=True, moderate_members=True,
            mute_members=True, deafen_members=True, move_members=True,
        )
    if key == "staff":
        return discord.Permissions(manage_messages=True, moderate_members=True,
                                   mute_members=True, deafen_members=True, move_members=True)
    return discord.Permissions.none()


def access(*, write=True, voice=False):
    return discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=write,
        attach_files=write, embed_links=write, add_reactions=write,
        create_public_threads=False, create_private_threads=False,
        send_messages_in_threads=write, mention_everyone=False,
        connect=voice, speak=voice, stream=voice, use_voice_activation=voice,
    )


def overwrites(guild, roles, policy="member", *, voice=False, owner=None, closed=False):
    is_entry = policy == "verification"
    result = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=is_entry, read_message_history=is_entry,
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
        roles["admin"]: access(voice=voice),
    }
    if policy == "log":
        for key in ("staff", "admin"):
            result[roles[key]] = access(write=False)
            result[roles[key]].manage_messages = False
            result[roles[key]].manage_threads = False
            result[roles[key]].manage_channels = False
            result[roles[key]].manage_webhooks = False
    if policy in ("member", "developer", "commands", "verification"):
        result[guild.default_role].use_application_commands = False
    if policy == "readonly":
        result[roles["member"]] = access(write=False)
    if is_entry:
        result[guild.default_role].create_instant_invite = False
        result[guild.default_role].use_external_apps = False
        result[roles["member"]] = discord.PermissionOverwrite(view_channel=False)
    if policy == "commands":
        for key in ("staff", "admin", "member"):
            result[roles[key]] = access()
            result[roles[key]].use_application_commands = True
        result[roles["external_bots"]] = access()
    writer = roles.get(policy)
    if writer:
        result[writer] = access(voice=voice)
        if policy == "developer":
            # The Developer role can post only when Member also grants visibility.
            result[writer].view_channel = None
    if owner is not None:
        result[owner] = access(write=not closed)
    return result


def layout_summary():
    lines = ["**Dial Dev Hub 서버 구성**", "역할: " + ", ".join(v[0] for v in ROLES.values())]
    for category in CATEGORIES:
        names = [c.name for c in CHANNELS if c.category == category.key]
        lines.append(f"• {category.name}: {', '.join(names) or '비공개 티켓 자동 생성'}")
    lines.append("\n초기 구성은 없는 항목만 만듭니다. 기존 서버의 가이드·질문·자료 정리는 `/ddh layout`에서 확인하세요.")
    return "\n".join(lines)
