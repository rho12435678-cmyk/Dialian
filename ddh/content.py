"""Community copy and durable, restart-safe panels."""
import discord

from ddh.blueprint import FIELDS
from ddh.server import UserError, channel_for

ORANGE = 0xF39C12


def embed(title, description):
    return discord.Embed(title=title, description=description, colour=ORANGE)


async def upsert_panel(channel, store, key, card, *, view=None):
    marker = f"DDH:{key}:v1"
    card.set_footer(text=marker)
    saved = await store.get(channel.guild.id, f"message:{key}")
    message = None
    if saved:
        try:
            candidate = await channel.fetch_message(saved)
            if candidate.author.id == channel.guild.me.id:
                message = candidate
        except discord.NotFound:
            pass
    if message is None:
        async for candidate in channel.history(limit=100):
            if candidate.author.id == channel.guild.me.id and any(e.footer.text == marker for e in candidate.embeds):
                message = candidate
                break
    if message:
        await message.edit(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
    else:
        message = await channel.send(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
    await store.set(channel.guild.id, f"message:{key}", message.id)
    return message


GUIDE_VERSION = "2026-11-owner-layout-guide-v2"


async def guide(guild, store):
    """Render the public first-visit guide with live links to DDH channels."""
    async def link(key):
        try:
            return (await channel_for(guild, store, key)).mention
        except UserError:
            return "해당 DDH 채널"

    verification = guild.get_channel(await store.get(guild.id, "channel:verification", 0))
    rules_link = await link("rules")
    start = (f"{rules_link}을 읽고 {verification.mention}에서 멤버 인증하기" if verification else
             f"{rules_link}의 버튼을 눌러 멤버 역할 받기")
    card = discord.Embed(
        title="🌐 DDH · Dial Dev Hub | 처음 오셨나요?",
        description=(
            "**Roblox 개발자와 프로젝트 제작자가 함께 성장하는 연결 공간**\n"
            "DDH는 작품을 소개하고, 개발자를 찾고, 협업 기회를 나누는 커뮤니티입니다.\n"
            "처음 방문했다면 이 가이드만 읽어도 활동을 시작할 수 있어요."
        ),
        colour=0x5865F2,
    )
    card.add_field(
        name="💡 DDH는 왜 만들어졌나요?",
        value=(
            "기존 DDS가 디자인 주문제작 중심이었다면, DDH는 그 범위를 Roblox 개발 전반으로 넓혔습니다.\n"
            "개발자는 포트폴리오와 실력을 알리고, 게임 제작자는 필요한 팀원이나 협업 상대를 "
            "더 쉽게 찾을 수 있도록 만들었습니다."
        ),
        inline=False,
    )
    card.add_field(
        name="🧩 여기서 무엇을 할 수 있나요?",
        value=(
            "• **포트폴리오 공유** — GFX · UI · 복장 · VFX · 모델링 · 빌딩 · 애니메이션 · 스크립팅\n"
            "• **구인·구직** — 프로젝트 팀원 모집, 개발 참여 기회 탐색\n"
            "• **제작의뢰** — 예산과 작업 범위를 적어 주문제작 개발자 찾기\n"
            "• **커뮤니티** — 개발 질문, 아이디어 공유, 작품 피드백, 공동 작업\n"
            "• **문의·신청** — 개발자 등록, 파트너 제안, 서버 개선 의견 제출"
        ),
        inline=False,
    )
    card.add_field(
        name="🚀 처음 들어왔다면 이 순서대로!",
        value=(
            f"**① 규칙 읽고 참여하기** — {start}\n"
            f"**② 사람들과 인사하기** — {await link('general')}에서 대화하고 {await link('questions')}에서 질문하기\n"
            f"**③ 개발자라면 등록하기** — {await link('support_panel')} → 개발자 신청 → 분야·포트폴리오 제출\n"
            f"**④ 함께할 사람 찾기** — {await link('hiring')}·{await link('commissions')}에서 "
            f"협업·주문제작 의뢰 확인, 개발자는 {await link('portfolio')}에서 작품·구직 정보 공유"
        ),
        inline=False,
    )
    card.add_field(
        name="📂 도움이 되는 채널",
        value=(
            f"**포트폴리오** {await link('portfolio')}  |  "
            f"**개발 질문** {await link('questions')}\n"
            f"**업데이트 공지** {await link('updates')}  |  "
            f"**문의·신청** {await link('support_panel')}"
        ),
        inline=False,
    )
    card.add_field(
        name="🛡️ 안전하고 편리한 활동을 위해",
        value=(
            "DDH는 직접 주문을 접수하거나 결제·납품을 대신 처리하지 않습니다. "
            "의뢰·협업 조건은 당사자들이 DM에서 합의하세요.\n"
            "작업 범위, 보상, 일정, 수정·환불 조건을 미리 명확히 하고, "
            "비밀번호·인증코드·토큰 같은 개인정보는 공유하지 마세요.\n"
            "도배, 작품 도용, 괴롭힘, 무단 서버 홍보는 허용되지 않습니다. "
            "문제가 생기면 **서버 피드백** 티켓으로 알려주세요."
        ),
        inline=False,
    )
    return card


async def publish_guide(bot, guild):
    """Create or edit one guide embed; never spam duplicate guide messages."""
    channel = await channel_for(guild, bot.store, "guide")
    message = await upsert_panel(channel, bot.store, "guide", await guide(guild, bot.store))
    await bot.store.set(guild.id, "guide:version", GUIDE_VERSION)
    return message


async def publish_verification(bot, guild):
    from ddh.views import VerificationView

    channel = await channel_for(guild, bot.store, "verification")
    rules = guild.get_channel(await bot.store.get(guild.id, "channel:rules", 0))
    rules_label = rules.mention if rules else "서버 규칙"
    age = bot.settings.verification_min_days
    requirement = f"\n• Discord 계정을 만든 지 {age}일 이상 지나야 해요." if age else ""
    panel = embed("DDH 멤버 인증",
                  f"• 먼저 {rules_label}을 읽고 Discord에서 요구하는 서버 규칙 확인을 마쳐 주세요."
                  f"{requirement}\n• 아래 버튼으로 규칙에 동의하면 멤버 역할을 받아요.")
    await upsert_panel(channel, bot.store, "verification", panel, view=VerificationView(bot))


async def publish_panels(bot, guild):
    from ddh.views import RulesView, SupportView, UpdateAlertsView

    verification = guild.get_channel(await bot.store.get(guild.id, "channel:verification", 0))
    instruction = (f"규칙을 읽은 뒤 {verification.mention}에서 인증해 주세요." if verification else
                   "규칙에 동의하고 아래 버튼을 눌러 멤버 인증을 진행해 주세요.")
    rules = embed("DDH에 오신 것을 환영합니다",
                  "**1. 서로 존중하기** — 괴롭힘·혐오·도배·타 서버 초대 광고를 금지합니다.\n"
                  "**2. 본인 작품만 게시하기** — 도용·사칭을 금지하며 공동작업은 본인의 담당 부분을 밝혀 주세요.\n"
                  "**3. 구인 내용을 명확히 쓰기** — 업무·보상·일정·연락 방법을 기재하고 무급/수익배분이면 표시하세요.\n"
                  "**4. 개인정보 보호하기** — 비밀번호·인증코드·쿠키·토큰·계좌 정보는 공개 채널에 올리지 마세요.\n"
                  "**5. 거래는 당사자끼리 협의하기** — DDH는 외주 제작팀이나 결제 중개 서비스가 아닙니다. "
                  "개발자 역할은 거래 이행이나 실력을 보증하지 않습니다.\n"
                  "**6. 문제가 생기면 문의하기** — 사기·도용·규칙 위반은 서버 피드백 티켓으로 알려 주세요.\n\n"
                  + instruction)
    support = embed("DDH 문의·신청",
                    "**파트너 문의**: 서버 소개와 제휴 제안\n"
                    "**서버 피드백**: 개선 제안·오류·규칙 위반 제보\n"
                    "**개발자 신청**: 분야를 선택하고 본인 포트폴리오 제출\n\n"
                    f"지원 분야: {' / '.join(FIELDS.values())}\n"
                    "신청 내용은 신청자와 운영진만 보는 티켓에 저장됩니다. "
                    "종료 시 대화 채널이 삭제되고 신청·처리 기록은 내부 DB에 남습니다.\n"
                    "진행 중인 티켓은 종류별로 1개까지 열 수 있습니다.")
    await upsert_panel(await channel_for(guild, bot.store, "rules"), bot.store, "rules", rules, view=RulesView(bot))
    await publish_guide(bot, guild)
    if await bot.store.get(guild.id, "channel:verification"):
        await publish_verification(bot, guild)
    await upsert_panel(await channel_for(guild, bot.store, "support_panel"), bot.store, "support", support, view=SupportView(bot))
    updates = embed(
        "🔔 DDH 업데이트 알림 센터",
        "새 기능, 개선 사항 및 운영 시스템 변경 내역을 이 채널에 안내합니다.\\n\\n"
        "**알림 받기**를 누르면 소유자가 업데이트 공지를 게시할 때 멘션 알림을 받을 수 있습니다.\\n"
        "원치 않으면 언제든지 **알림 끄기**를 눌러 해제하세요.\\n"
        "같은 버전의 봇 재시작으로 알림이 중복 발송되지 않습니다.",
    )
    await upsert_panel(
        await channel_for(guild, bot.store, "updates"),
        bot.store, "update-alerts", updates, view=UpdateAlertsView(bot),
    )
    # Hiring, looking-for-work, commissions, and portfolio channels are Forums.
    # Their channel descriptions act as posting guidelines. Forum channels do
    # not support sending a standalone text message as an onboarding panel.
