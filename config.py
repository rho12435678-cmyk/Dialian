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
PURCHASE_CHANNEL_ID = 1505102694917079132 # 구매 채널
DESIGNER_STATS_CHANNEL_ID = 1521001578239361155  # '디자이너-통계' 채널 ID
REVIEWS_CHANNEL_ID = 1506517440463638581 # '후기' 채널 ID
COMMAND_CHANNEL_ID = 1531287070281040054 # 🌟 명령어/미니게임 전용 채널 ID

BUYER_ROLE_ID = 1505076370332586155 # 구매자 역할
CUSTOMER_ROLE_ID = 1505074732700008531 # 손님 역할


# ==========================
# 개발자 ID
# ==========================

DESIGNER_ROLE_IDS = {
    "gfx": 1518906536095776868,      # GFX 디자이너 역할 ID
    "uniform": 1522539025691312168,  # 복장 디자이너 역할 ID
}

# ==================== [포인트 및 단골 시스템 설정] ====================
WORK_SHARE_CHANNEL_ID = 1505111260595879986  # #작품공유 채널 ID
FEEDBACK_CHANNEL_ID = 1505111362919989418  # #피드백 채널 ID
REGULAR_CUSTOMER_ROLE_ID = 1510482073838686308 # 단골 손님 역할 ID

TARGET_REGULAR_POINTS = 500  # 골드(단골) 승급 기준 포인트
REGULAR_DISCOUNT_RATE = 0.15 # 15% 할인율

# ==================== [활동 및 미니게임 세부 설정] ====================
DAILY_ACTION_LIMIT = 3       # 작품공유 및 피드백 일일 최대 적립 제한 횟수
WORK_SHARE_POINTS = 15       # 작품공유 1회 적립 포인트
FEEDBACK_POINTS = 10         # 피드백 반응 1회 적립 포인트
GACHA_COST = 20              # 뽑기 1회 소모 포인트
