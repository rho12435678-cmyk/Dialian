"""Compact, shared ticket instructions for GFX, clothing and UI."""
import discord


def build_ticket_notice_embed(category="GFX", quote=None, variation_unit=None):
    """One mobile-friendly price, policy and reference embed per commission."""
    embed = discord.Embed(
        title="📌 DDS 커미션 안내",
        description=(
            "**진행 순서**\n"
            "① 참고자료 업로드 → ② 견적 확인·선결제 → "
            "③ 제작 → ④ 완성 후 후기"
        ),
        color=discord.Color.blurple(),
    )
    if quote is None:
        price = "담당자 배정·등급 확인 후 최종 가격을 안내합니다."
    else:
        price = (
            f"기준가 {quote['base']:,}원 · 할인 {quote['rate']}%\n"
            f"**예상 결제액 {quote['total']:,}원**"
        )
        if variation_unit is not None:
            price += f"\n복장 바리에이션 **개당 {variation_unit:,}원** 별도"

    embed.add_field(
        name="💳 예상 가격",
        value=price + "\n※ 담당자 최종 확인 전에는 입금하지 마세요.",
        inline=False,
    )
    needed = (
        "구도·배경·분위기·색감" if category == "GFX" else
        "의상 앞/뒷면·색상·장식" if category == "Roblox 복장" else
        "UI 화면·버튼·배치·색상"
    )
    embed.add_field(
        name="🖼️ 지금 할 일",
        value=(
            f"이 채널에 **참고 이미지/파일**과 원하는 {needed}을 "
            "올려주세요. 필요한 모델·에셋 자료도 함께 첨부해 주세요."
        ),
        inline=False,
    )
    cancel = (
        "상급 3,000원 / 중급 2,000원 / 초급 1,500원"
        if category == "GFX" else
        "1,500원" if category == "Roblox 복장" else
        "UI 적용 조건·금액은 담당자에게 사전 확인"
    )
    embed.add_field(
        name="📎 이용 규정",
        value=(
            "• 가격 협상 불가 · 작업 중 과도한 수정 요청 삼가\n"
            "• 작업 시작 전 철회: 전액 환불\n"
            f"• 작업 시작 후 철회: {cancel}"
        ),
        inline=False,
    )
    embed.set_footer(text="가격·작업 범위·추가 수정은 결제 전에 담당자와 확인해 주세요.")
    return embed
