"""Community copy and durable, restart-safe panels."""
import discord

from ddh.server import UserError, channel_for

ORANGE = 0xF39C12


def embed(title, description):
    return discord.Embed(title=title, description=description, colour=ORANGE)


async def upsert_panel(channel, store, key, card, *, view=None, content=None, allowed_mentions=None):
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
    extra = {"content": content} if content is not None else {}
    if message:
        await message.edit(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none(), **extra)
    else:
        message = await channel.send(embed=card, view=view, allowed_mentions=allowed_mentions or discord.AllowedMentions.none(), **extra)
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


PANELS_VERSION = "2026-11-commission-tickets-v3"
GUIDE_VERSION = "2026-11-commission-guide-v3"


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
            f"**① 인증 후 규칙 확인하기** — {await link('verification')}에서 인증하고 {await link('rules')} 읽기\n"
            f"**② 사람들과 인사하기** — {await link('general')}에서 대화하고 {await link('questions')}에서 질문하기\n"
            f"**③ 개발자라면 등록하기** — {await link('support_panel')} → 개발자 신청 → 분야·포트폴리오 제출\n"
            f"**④ 함께할 사람 찾기** — {await link('hiring')}에서 팀원을 찾고 {await link('commission_panel')}의 작품 의뢰 시작 버튼으로 "
            f"작업을 요청하세요. 개발자는 {await link('portfolio')}에서 작품·구직 정보 공유"
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
            "의뢰는 DDH 비공개 티켓으로 접수하며 결제·제작·납품은 담당 개발자와 직접 진행합니다. "
            "제작 의뢰의 금액·납기·수정 조건은 담당 개발자와 비공개 티켓에서 합의하세요.\n"
            "작업 범위, 보상, 일정, 수정·환불 조건을 미리 명확히 하고, "
            "비밀번호·인증코드·토큰 같은 개인정보는 공유하지 마세요.\n"
            "도배, 작품 도용, 괴롭힘, 무단 서버 홍보는 허용되지 않습니다. "
            "문제 발생 시 **문의-support**에서 거래·도용 신고 버튼을 이용해 비공개로 알려주세요."
        ),
        inline=False,
    )
    card.add_field(
        name="거래 채널 이용 권한",
        value="구인: 인증된 멤버와 개발자 모두 게시·댓글 가능\n제작 의뢰: 의뢰안내 채널에서 분야별 티켓 접수\n"
              "포트폴리오: 승인된 개발자가 작품을 게시하고 인증된 멤버는 열람·댓글 가능\n"
              "실제 거래 조건과 연락은 당사자끼리 협의해 주세요.",
        inline=False,
    )
    return card


async def publish_guide(bot, guild):
    """Create or edit one guide embed; never spam duplicate guide messages."""
    channel = await channel_for(guild, bot.store, "guide")
    message = await upsert_panel(
        channel, bot.store, "guide", await guide(guild, bot.store),
    )
    await bot.store.set(guild.id, "guide:version", GUIDE_VERSION)
    return message


def _panel_has_required_buttons(message, key):
    """Check actual Discord component IDs, not just an embed's footer marker."""
    expected = {
        "verification": {"ddh:rules:v1"},
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


async def repair_legacy_verification_panels(bot, guild):
    """Reconnect old DDH:verification:v1 buttons to the current persistent view.

    Some DDH installations have a separate older #인증 channel/panel. Its
    message predates the managed rules/guide panels and used a different
    custom_id, so simply repairing the modern buttons cannot fix it.
    Only edit Dialian-authored messages with the exact old footer marker.
    """
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False

    from ddh.views import RulesView

    marker = "DDH:verification:v1"
    saved_id = await bot.store.get(guild.id, "message:verification")
    from ddh.visibility import find_verification_channel
    try:
        channels = [await find_verification_channel(guild, bot.store)]
    except UserError:
        return False
    changed = False
    for channel in channels:
        messages = []
        if saved_id:
            try:
                messages.append(await channel.fetch_message(saved_id))
            except discord.NotFound:
                pass
            except discord.Forbidden:
                continue

        seen = {message.id for message in messages}
        try:
            async for message in channel.history(limit=200):
                if message.id not in seen:
                    messages.append(message)
                    seen.add(message.id)
        except discord.Forbidden:
            continue

        for message in messages:
            if message.author.id != guild.me.id or not any(
                embed.footer.text == marker for embed in message.embeds
            ):
                continue
            if not _panel_has_required_buttons(message, "verification"):
                await message.edit(
                    embed=message.embeds[0],
                    view=RulesView(bot),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                changed = True
            await bot.store.set(guild.id, "message:verification", message.id)
    return changed


async def repair_missing_panels(bot, guild):
    """Self-heal missing public guides/forms, without changing server layout.

    Stable message IDs are checked first; the complete set is only refreshed if
    a panel was deleted. No role/channels are created and no users are pinged.
    """
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False
    repaired_legacy = await repair_legacy_verification_panels(bot, guild)
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
    return repaired_legacy


async def publish_panels(bot, guild):
    rules = embed("DDH에 오신 것을 환영합니다",
                  "**1. 서로 존중하기** — 괴롭힘·혐오·도배·타 서버 초대 광고를 금지합니다.\n"
                  "**2. 본인 작품만 게시하기** — 도용·사칭을 금지하며 공동작업은 본인의 담당 부분을 밝혀 주세요.\n"
                  "**3. 구인 내용을 명확히 쓰기** — 업무·보상·일정·연락 방법을 기재하고 무급/수익배분이면 표시하세요.\n"
                  "**4. 개인정보 보호하기** — 비밀번호·인증코드·쿠키·토큰·계좌 정보는 공개 채널에 올리지 마세요.\n"
                  "**5. 거래는 당사자끼리 협의하기** — DDH는 외주 제작팀이나 결제 중개 서비스가 아닙니다. "
                  "개발자 역할은 거래 이행이나 실력을 보증하지 않습니다.\n"
                  "**6. 문제가 생기면 신고하기** — 사기·도용·규칙 위반은 거래·도용 신고 버튼으로 비공개로 알려 주세요.\n\n"
                  "멤버 인증은 **인증 채널**에서만 진행합니다. "
                  f"계정 생성 후 {bot.settings.verification_min_days}일 이상인 계정은 조건 확인 후 "
                  "멤버 역할을 받습니다. 인증이 거절되면 표시되는 이유를 확인해 주세요.")
    await upsert_panel(await channel_for(guild, bot.store, "rules"), bot.store, "rules", rules)
    await publish_guide(bot, guild)
    await publish_support(bot, guild)
    from ddh.commission_hub import publish_commission_hub
    await publish_commission_hub(bot, guild)
    await bot.store.set(guild.id, "panels:version", PANELS_VERSION)
    # The old alert opt-in panel is retired. Release posts are automatic.
    await retire_update_alerts_panel(bot, guild)
    # Hiring and portfolio channels are Forums; commissions use private tickets.
    # Their channel descriptions act as posting guidelines. Forum channels do
    # not support sending a standalone text message as an onboarding panel.


async def configure_verification_entry(bot, guild, verification):
    """Explicit owner request only: remove auth controls, keeping all message text."""
    from ddh.views import RulesView

    for key in ("rules", "guide"):
        channel = await channel_for(guild, bot.store, key)
        async for message in channel.history(limit=None):
            if message.author.id != guild.me.id:
                continue
            view = discord.ui.View.from_message(message, timeout=None)
            removed = False
            for item in list(view.children):
                custom_id = getattr(item, "custom_id", "") or ""
                if custom_id == "ddh:rules:v1" or custom_id.startswith("ddh:verification:"):
                    view.remove_item(item)
                    removed = True
            if removed:
                await message.edit(view=view if view.children else None)

    await upsert_panel(
        verification, bot.store, "verification",
        embed("DDH 멤버 인증", "DDH는 Roblox 개발자와 게임 제작자를 연결하는 커뮤니티입니다.\n\n"
              "**인증 전 확인**\n"
              "• 서로 존중하고 괴롭힘·도배·무단 광고를 하지 마세요.\n"
              "• 본인 작품만 게시하고 도용·사칭을 하지 마세요.\n"
              "• 개인정보를 보호하고 거래 조건은 당사자끼리 확인하세요.\n\n"
              "위 내용에 동의하면 아래 버튼을 눌러 주세요. 인증 후 멤버 역할이 지급되고 "
              "규칙·가이드와 활동 채널이 열립니다.\n"
              f"계정 생성 후 {bot.settings.verification_min_days}일 이상이어야 하며, "
              "Discord 기본 규칙 동의가 필요한 경우 먼저 완료해 주세요."),
        view=RulesView(bot),
    )


async def publish_support(bot, guild):
    from ddh.views import SupportView
    hub_id = await bot.store.get(guild.id, "channel:commission_panel")
    hub = guild.get_channel(hub_id) if hub_id else None
    commission_hint = f"**작품 제작 의뢰**는 {hub.mention}에서 신청 방법·가격 안내를 확인하고 접수하세요.\n\n" if hub else ""
    card = embed("DDH 문의·신청",
        commission_hint
        + "**개발자 신청** — 분야 선택 후 작업물을 티켓에 첨부할 수 있습니다.\n"
        "**파트너 문의 / 서버 피드백 / 거래·도용 신고**는 아래에서 접수하세요.\n\n"
        "일반 문의는 신청자·운영진만 열람합니다. 일반 신청자는 직접 종료할 수 없으며 운영진이 종료합니다.\n"
        "문의 종료 시 채널이 삭제되고 신청·처리 기록은 내부 DB에 남습니다.")
    await upsert_panel(await channel_for(guild, bot.store, "support_panel"), bot.store, "support", card, view=SupportView(bot))
