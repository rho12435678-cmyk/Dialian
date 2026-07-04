import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("TOKEN")

# ==========================
# 서버 설정
# ==========================

REVIEW_CHANNEL_NAME = "후기" # 후기 채널
LOG_CHANNEL_NAME = "구매로그" # 구매로그 채널
ARCHIVE_CATEGORY_NAME = "티켓 아카이브" # 티켓 보관함 채널

SALE_NOTICE_CHANNEL_ID = 1505562851824369714 # 구매 알림 채널
EXAMPLE_CHANNEL_ID = 1505178799950532720 # 예시작 채널
PURCHASE_CHANNEL_ID = 1522338323245436988 # 구매 채널

BUYER_ROLE_ID = 1505076370332586155 # 구매자 역할
CUSTOMER_ROLE_ID = 1505074732700008531 # 손님 역할


# ==========================
# 개발자 ID
# ==========================

DESIGNER_ROLE_IDS = {
    "gfx": 1518906536095776868,      # GFX 디자이너 역할 ID
    "logo": 1522538883898671165,     # 로고 디자이너 역할 ID
    "uniform": 1522539025691312168,  # 복장 디자이너 역할 ID
}
