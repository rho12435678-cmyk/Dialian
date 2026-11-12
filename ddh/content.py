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


async def retire_update_alerts_panel(bot, guild):
    """Remove only Dialian's old opt-in/out panel; preserve actual releases.

    Both the saved message ID and the footer marker are checked. A durable
    flag makes repeated maintenance checks no-ops after successful cleanup.
    """
    if await bot.store.get(guild.id, "updates:panel_retired", False):
        return False
    channel = await channel_for(guild, bot.store, "updates")
    marker = "DDH:update-alerts:v1"
    saved = await bot.store.get(guild.id, "message:update-alerts")
    messages = {}

    if saved:
        try:
            item = await channel.fetch_message(saved)
            if item.author.id == guild.me.id and any(
                embed.footer.text == marker for embed in item.embeds
            ):
                messages[item.id] = item
        except discord.NotFound:
            pass

    async for item in channel.history(limit=200):
        if item.author.id == guild.me.id and any(
            embed.footer.text == marker for embed in item.embeds
        ):
            messages[item.id] = item

    for item in messages.values():
        await item.delete()
    await bot.store.set(guild.id, "message:update-alerts", None)
    await bot.store.set(guild.id, "updates:panel_retired", True)
    return bool(messages)


PANELS_VERSION = "2026-11-verify-and-public-notice-v2"
GUIDE_VERSION = "2026-11-owner-layout-guide-v2"


async def guide(guild, store):
    """Render the public first-visit guide with live links to DDH channels."""
    async def link(key):
        try:
            return (await channel_for(guild, store, key)).mention
        except UserError:
            return "해당 DDH 채널"

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
            f"**① 규칙 읽고 참여하기** — {await link('rules')}의 버튼을 눌러 멤버 역할 받기\n"
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
            "문제 발생 시 **문의-support**에서 거래·도용 신고 버튼을 이용해 비공개로 알려주세요."
        ),
        inline=False,
    )
    return card


async def publish_guide(bot, guild):
    """Create or edit one guide embed; never spam duplicate guide messages."""
    channel = await channel_for(guild, bot.store, "guide")
    from ddh.views import RulesView

    # Give members a second, persistent path to certification when the
    # native Discord Server Guide changes where the rules panel is shown.
    message = await upsert_panel(
        channel, bot.store, "guide", await guide(guild, bot.store),
        view=RulesView(bot),
    )
    await bot.store.set(guild.id, "guide:version", GUIDE_VERSION)
    return message


def _panel_has_required_buttons(message, key):
    """Check actual Discord component IDs, not just an embed's footer marker."""
    expected = {
        "rules": {"ddh:rules:v1"},
        "guide": {"ddh:rules:v1"},
        "support": {
            "ddh:partner:v1", "ddh:feedback:v1",
            "ddh:developer:v1", "ddh:report:v1",
        },
    }.get(key, set())
    if not expected:
        return True
    # Fake messages in integration tests keep the edited view; real REST
    # messages expose ActionRows in message.components.
    rows = getattr(message, "components", None)
    components = []
    if rows is not None:
        for row in rows:
            components.extend(getattr(row, "children", ()))
    else:
        view = getattr(message, "view", None)
        components.extend(getattr(view, "children", ()) if view else ())
    present = {
        getattr(item, "custom_id", None)
        for item in components if not getattr(item, "disabled", False)
    }
    return expected <= present


async def repair_missing_panels(bot, guild):
    """Self-heal missing public guides/forms, without changing server layout.

    Stable message IDs are checked first; the complete set is only refreshed if
    a panel was deleted. No role/channels are created and no users are pinged.
    """
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False
    if await bot.store.get(guild.id, "panels:version") != PANELS_VERSION:
        async with bot.mutation_lock:
            await publish_panels(bot, guild)
        return True
    for key, channel_key in (
        ("rules", "rules"),
        ("guide", "guide"),
        ("support", "support_panel"),
    ):
        channel = await channel_for(guild, bot.store, channel_key)
        saved = await bot.store.get(guild.id, f"message:{key}")
        if saved:
            try:
                message = await channel.fetch_message(saved)
                if (message.author.id == guild.me.id
                        and any(e.footer.text == f"DDH:{key}:v1" for e in message.embeds)
                        and _panel_has_required_buttons(message, key)):
                    continue
            except discord.NotFound:
                pass
        async with bot.mutation_lock:
            await publish_panels(bot, guild)
        return True
    return False


async def publish_panels(bot, guild):
    from ddh.views import RulesView, SupportView

    rules = embed("DDH에 오신 것을 환영합니다",
                  "**1. 서로 존중하기** — 괴롭힘·혐오·도배·타 서버 초대 광고를 금지합니다.\n"
                  "**2. 본인 작품만 게시하기** — 도용·사칭을 금지하며 공동작업은 본인의 담당 부분을 밝혀 주세요.\n"
                  "**3. 구인 내용을 명확히 쓰기** — 업무·보상·일정·연락 방법을 기재하고 무급/수익배분이면 표시하세요.\n"
                  "**4. 개인정보 보호하기** — 비밀번호·인증코드·쿠키·토큰·계좌 정보는 공개 채널에 올리지 마세요.\n"
                  "**5. 거래는 당사자끼리 협의하기** — DDH는 외주 제작팀이나 결제 중개 서비스가 아닙니다. "
                  "개발자 역할은 거래 이행이나 실력을 보증하지 않습니다.\n"
                  "**6. 문제가 생기면 신고하기** — 사기·도용·규칙 위반은 거래·도용 신고 버튼으로 비공개로 알려 주세요.\n\n"
                  "먼저 Discord 서버 규칙 동의를 완료한 뒤 아래 **멤버 인증** 버튼을 누르세요. "
                  f"계정 생성 후 {bot.settings.verification_min_days}일 이상인 계정은 조건 확인 후 "
                  "멤버 역할을 받습니다. 인증이 거절되면 표시되는 이유를 확인해 주세요.")
    support = embed("DDH 문의·신청",
                    "**파트너 문의**: 서버 소개와 제휴 제안\n"
                    "**서버 피드백**: 서버 개선 제안·오류 제보\n"
                    "**거래·도용 신고**: 의뢰 분쟁, 사칭, 작품 도용 등 문제를 비공개 접수\n"
                    "**개발자 신청**: 분야를 선택하고 본인 포트폴리오 제출\n\n"
                    f"지원 분야: {' / '.join(FIELDS.values())}\n"
                    "신청 내용은 신청자와 운영진만 보는 티켓에 저장됩니다. "
                    "종료 시 대화 채널이 삭제되고 신청·처리 기록은 내부 DB에 남습니다.\n"
                    "진행 중인 티켓은 종류별로 1개까지 열 수 있습니다.")
    await upsert_panel(await channel_for(guild, bot.store, "rules"), bot.store, "rules", rules, view=RulesView(bot))
    await publish_guide(bot, guild)
    await upsert_panel(await channel_for(guild, bot.store, "support_panel"), bot.store, "support", support, view=SupportView(bot))
    await bot.store.set(guild.id, "panels:version", PANELS_VERSION)
    # The old alert opt-in panel is retired. Release posts are automatic.
    await retire_update_alerts_panel(bot, guild)
    # Hiring, looking-for-work, commissions, and portfolio channels are Forums.
    # Their channel descriptions act as posting guidelines. Forum channels do
    # not support sending a standalone text message as an onboarding panel.
