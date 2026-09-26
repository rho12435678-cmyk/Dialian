"""Small, mobile-friendly price boards for the three-level DDS enquiry panel.

All displayed amounts use the same base prices and discount calculation as
commission quotations. Viewing a public price board does not grant a discount.
"""
import discord

from config import TARGET_REGULAR_POINTS
from database.modal.ui_modal import UI_BASE
from database.services.commission_pricing import (
    GFX_PRICES, UNIFORM_PRICES, UNIFORM_VARIATION_BASE,
)
from database.services.family import discount_rate, discounted_price

PRICE_PROFILES = {
    "standard": (False, False),
    "regular": (False, True),
    "family": (True, False),
    "combined": (True, True),
}
PRICE_TITLES = {
    "standard": "📋 DDS 일반 가격표",
    "regular": "⭐ DDS 단골 전용 가격표 · 20% 할인",
    "family": "💎 DDS FAMILY 전용 가격표 · 20% 할인",
    "combined": "✨ DDS FAMILY + 단골 가격표 · 총 30% 할인",
}
PRICE_DESCRIPTIONS = {
    "standard": "모든 회원이 확인할 수 있는 기본 가격입니다.",
    "regular": (
        f"단골 역할 보유 회원에게 적용되는 가격입니다. "
        f"(기준: {TARGET_REGULAR_POINTS:,}P)"
    ),
    "family": "이용 기간이 유효한 FAMILY 회원에게 적용되는 가격입니다.",
    "combined": (
        "유효한 FAMILY와 단골 역할을 모두 보유한 회원에게 적용됩니다. "
        "할인율은 합계 30%이며 40% 중복 할인은 적용되지 않습니다."
    ),
}


def format_package_prices(prices, *, family=False, regular=False):
    """The exact same discount math as the live commission quote."""
    amounts = [
        discounted_price(base, family=family, regular=regular)
        for base in prices
    ]
    return (
        f"단품 **{amounts[0]:,}원**\n"
        f"2+1 묶음 **{amounts[1]:,}원**\n"
        f"3+1 묶음 **{amounts[2]:,}원**"
    )


def variation_price(profile):
    """One optional uniform colour/design variation; no bundle pricing."""
    family, regular = PRICE_PROFILES[profile]
    return discounted_price(UNIFORM_VARIATION_BASE, family, regular)


def build_price_embed(profile):
    """Format every profile identically so new customers can compare easily."""
    if profile not in PRICE_PROFILES:
        raise ValueError("Unknown DDS pricing profile")
    family, regular = PRICE_PROFILES[profile]
    rate = discount_rate(family, regular)

    embed = discord.Embed(
        title=PRICE_TITLES[profile],
        description=(
            f"{PRICE_DESCRIPTIONS[profile]}\n\n"
            "📦 **구성 안내**\n"
            "• 단품: 1개 제작\n"
            "• 2+1: 2개 가격으로 총 3개 제작\n"
            "• 3+1: 3개 가격으로 총 4개 제작"
        ),
        color=discord.Color.teal() if family else (
            discord.Color.gold() if regular else discord.Color.blue()
        ),
    )
    for tier, base_prices in GFX_PRICES.items():
        embed.add_field(
            name=f"🎨 {tier} GFX",
            value=format_package_prices(base_prices, family=family, regular=regular),
            inline=True,
        )
    embed.add_field(
        name="👕 Roblox 복장 (상의/하의 개별)",
        value=format_package_prices(
            UNIFORM_PRICES, family=family, regular=regular
        ),
        inline=False,
    )
    embed.add_field(
        name="🎨 복장 바리에이션 (추가 변형)",
        value=(
            f"색상/디자인 변형 추가 시 **개당 {variation_price(profile):,}원**\n"
            "※ 기본 복장 가격과 별도로 추가됩니다."
        ),
        inline=False,
    )
    if family:
        embed.add_field(
            name="🖥️ Roblox UI 사전 커미션",
            value=format_package_prices(
                tuple(UI_BASE.values()), family=family, regular=regular
            ) + "\n※ FAMILY 회원만 신청할 수 있습니다.",
            inline=False,
        )
    else:
        embed.add_field(
            name="🖥️ Roblox UI 안내",
            value="현재 UI 사전 커미션은 FAMILY 회원 전용입니다.",
            inline=False,
        )

    embed.set_footer(
        text=(
            f"적용 할인율 {rate}% · 주문 시점 자격에 따라 최종 혜택이 결정됩니다. "
            "최종 금액은 담당자와 확인해 주세요."
        )
    )
    return embed
