"""Centralized provisional quote for DDS GFX and clothing tickets."""
from config import REGULAR_CUSTOMER_ROLE_ID
from database.services.family import discount_rate, discounted_price, is_family_active
from database.services.ticket_layout import designer_tier

GFX_PRICES = {
    "초급": (5000, 10000, 15000),
    "중급": (6500, 13000, 19500),
    "상급": (8500, 17000, 25500),
}
UNIFORM_PRICES = (5000, 10000, 15000)
# Optional colour/design variation added to a uniform order, per variation.
UNIFORM_VARIATION_BASE = 500

def bundle_index(name):
    if "3+1" in str(name):
        return 2
    if "2+1" in str(name):
        return 1
    return 0

async def quote_for(category, bundle, member, designer=None):
    """Order-time quotation; unassigned GFX tiers require manual confirmation."""
    if category == "GFX":
        tier = designer_tier(designer, category)
        price_table = GFX_PRICES.get(tier)
        if price_table is None:
            return None
    elif category == "Roblox 복장":
        price_table = UNIFORM_PRICES
    else:
        return None
    family = await is_family_active(member)
    regular = any(r.id == REGULAR_CUSTOMER_ROLE_ID for r in member.roles)
    base = price_table[bundle_index(bundle)]
    return {
        "base": base,
        "rate": discount_rate(family, regular),
        "total": discounted_price(base, family, regular),
        "family": family,
        "regular": regular,
    }
