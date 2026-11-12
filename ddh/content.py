"""Community copy and durable, restart-safe panels."""
import discord

from ddh.blueprint import FIELDS
from ddh.server import channel_for

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


async def guide(guild, store):
    async def link(key):
        return (await channel_for(guild, store, key)).mention
    return embed("DDH · Dial Dev Hub 이용 안내",
                 "Roblox 개발자가 작품을 소개하고 팀과 동료를 찾는 커뮤니티입니다.\n\n"
                 f"**시작**: {await link('rules')}에서 규칙에 동의하면 채팅·구인·음성 채널을 이용할 수 있습니다.\n"
                 f"**개발자 등록**: {await link('support_panel')} → 분야 선택 → 포트폴리오 제출 → 운영진 심사.\n"
                 "**포트폴리오**: 승인된 분야 채널에 본인의 작업물과 연락 방법을 올려 주세요.\n"
                 f"**구인·구직**: {await link('hiring')} / {await link('looking')}에서 연결된 뒤 당사자 DM으로 협의하세요.\n"
                 f"**문의**: {await link('support_panel')}에서 파트너·서버 피드백·개발자 신청을 선택하세요.\n\n"
                 "DDH는 직접 주문을 접수하거나 대금을 보관하지 않습니다. "
                 "작업 범위·금액·납기·수정·환불 조건은 당사자끼리 합의하고 기록을 남겨 주세요.")


async def publish_panels(bot, guild):
    from ddh.views import RulesView, SupportView

    rules = embed("DDH에 오신 것을 환영합니다",
                  "**1. 서로 존중하기** — 괴롭힘·혐오·도배·타 서버 초대 광고를 금지합니다.\n"
                  "**2. 본인 작품만 게시하기** — 도용·사칭을 금지하며 공동작업은 본인의 담당 부분을 밝혀 주세요.\n"
                  "**3. 구인 내용을 명확히 쓰기** — 업무·보상·일정·연락 방법을 기재하고 무급/수익배분이면 표시하세요.\n"
                  "**4. 개인정보 보호하기** — 비밀번호·인증코드·쿠키·토큰·계좌 정보는 공개 채널에 올리지 마세요.\n"
                  "**5. 거래는 당사자끼리 협의하기** — DDH는 외주 제작팀이나 결제 중개 서비스가 아닙니다. "
                  "개발자 역할은 거래 이행이나 실력을 보증하지 않습니다.\n"
                  "**6. 문제가 생기면 문의하기** — 사기·도용·규칙 위반은 서버 피드백 티켓으로 알려 주세요.\n\n"
                  "아래 버튼으로 규칙에 동의하면 멤버 역할을 받습니다.")
    support = embed("DDH 문의·신청",
                    "**파트너 문의**: 서버 소개와 제휴 제안\n"
                    "**서버 피드백**: 개선 제안·오류·규칙 위반 제보\n"
                    "**개발자 신청**: 분야를 선택하고 본인 포트폴리오 제출\n\n"
                    f"지원 분야: {' / '.join(FIELDS.values())}\n"
                    "신청 내용은 신청자와 운영진만 보는 티켓에 저장됩니다. "
                    "종료한 티켓은 운영진 전용 보관함에 남습니다. 삭제 요청은 운영진에게 알려 주세요.\n"
                    "진행 중인 티켓은 종류별로 1개까지 열 수 있습니다.")
    await upsert_panel(await channel_for(guild, bot.store, "rules"), bot.store, "rules", rules, view=RulesView(bot))
    await upsert_panel(await channel_for(guild, bot.store, "guide"), bot.store, "guide", await guide(guild, bot.store))
    await upsert_panel(await channel_for(guild, bot.store, "support_panel"), bot.store, "support", support, view=SupportView(bot))
    for key, title, text in (
        ("hiring", "구인 작성 양식", "프로젝트 / 모집 분야 / 업무 범위 / 예산·보상 방식 / 일정 / 지원 조건 / 연락 방법\n"
         "무급 또는 수익배분이면 명확하게 표시하세요. 협의와 계약은 당사자 DM에서 진행합니다."),
        ("looking", "구직 작성 양식", "개발 분야 / 경력·도구 / 포트폴리오 / 가능한 업무 / 가능 일정 / 연락 방법"),
        *[(f"portfolio:{field}", f"{label} 포트폴리오 작성 양식",
           "작품명 / 이미지·영상·링크 / 본인 담당 부분 / 사용 도구 / 작업 가능한 범위 / 연락 방법\n"
           "공동작업·AI 활용 여부 등 작품을 이해하는 데 필요한 정보를 함께 밝혀 주세요.") for field, label in FIELDS.items()],
    ):
        await upsert_panel(await channel_for(guild, bot.store, key), bot.store, key, embed(title, text))
