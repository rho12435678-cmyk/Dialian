import os

TOKEN = os.getenv("TOKEN")

# ==============================
# 역할(Role) ID
# ==============================

BUYER_ROLE_ID = 1505076370332586155
CUSTOMER_ROLE_ID = 1505074732700008531

# ==============================
# 채널(Channel) ID
# ==============================

ANNOUNCE_CHANNEL_ID = 1505562851824369714

REVIEW_CHANNEL_NAME = "후기"
LOG_CHANNEL_NAME = "구매로그"

# ==============================
# 개발자 정보
# ==============================

DEVELOPERS = {

    # GFX + 복장
    375938495350571009: {
        "name": "Designer1",
        "types": ["gfx", "uniform"]
    },

    # GFX + 로고
    1292859064065458189: {
        "name": "Designer2",
        "types": ["gfx", "logo"]
    },

    # GFX
    1465051418162626763: {
        "name": "Designer3",
        "types": ["gfx"]
    },

    # GFX
    1468584582113919129: {
        "name": "Designer4",
        "types": ["gfx"]
    }

}
