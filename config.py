import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("TOKEN")

# ==========================================
# 1. 일반 채널 및 카테고리 이름 설정
# ==========================================
REVIEW_CHANNEL_NAME = "후기"         # 후기 채널 이름
LOG_CHANNEL_NAME = "구매로그"        # 구매로그 채널 이름
ARCHIVE_CATEGORY_NAME = "티켓 아카이브" # 티켓 보관함 카테고리 이름

# ==========================================
# 2. 주요 채널 ID 설정
# ==========================================
# [커미션 & 안내 관련 채널]
PURCHASE_CHANNEL_ID = 1505102694917079132        # 구매/문의 채널
EXAMPLE_CHANNEL_ID = 1505178799950532720         # 예시작 채널
REVIEWS_CHANNEL_ID = 1506517440463638581         # 후기 채널
SALE_NOTICE_CHANNEL_ID = 1505562851824369714     # 구매 알림 채널
DESIGNER_STATS_CHANNEL_ID = 1521001578239361155  # 디자이너 통계 채널
DESIGNER_TIER_CHANNEL_ID = 1537806140711239760   # 디자이너 등급 채널

# [포인트 & 활동 관련 채널]
POINT_RANKING_CHANNEL_ID = 1532599012316938321   # 포인트 랭킹 채널
POINT_INFO_CHANNEL_ID = 1532373833783316610      # 포인트 적립/안내 채널
WORK_SHARE_CHANNEL_ID = 1505111260595879986      # 작품공유 채널
FEEDBACK_CHANNEL_ID = 1505111362919989418        # 피드백 채널
COMMAND_CHANNEL_ID = 1531287070281040054         # 명령어/미니게임 전용 채널

# [12시간 자동 가이드 채팅 채널]
KR_CHAT_CHANNEL_ID = 1505074223356317771         # 한국어 채팅 채널
EN_CHAT_CHANNEL_ID = 1527725232864100362         # 영어 채팅 채널

# ==========================================
# 3. 역할 ID 설정
# ==========================================
BUYER_ROLE_ID = 1505076370332586155              # 구매자 역할 ID
CUSTOMER_ROLE_ID = 1505074732700008531           # 손님 역할 ID
REGULAR_CUSTOMER_ROLE_ID = 1510482073838686308   # 단골 손님 역할 ID

# 디자이너 분야별 역할 ID
DESIGNER_ROLE_IDS = {
    "gfx": 1518906536095776868,      # GFX 디자이너 역할 ID
    "uniform": 1522539025691312168,  # 복장 디자이너 역할 ID
}

# ==========================================
# 4. 포인트 & 미니게임 상세 정책 설정
# ==========================================
TARGET_REGULAR_POINTS = 1000   # 단골 승급 기준 포인트
REGULAR_DISCOUNT_RATE = 0.15   # 단골 할인율 (15%)

DAILY_ACTION_LIMIT = 3         # 일일 적립 제한 횟수 (작품공유/피드백)
WORK_SHARE_POINTS = 15         # 작품공유 1회 적립 포인트
FEEDBACK_POINTS = 10           # 피드백 반응 1회 적립 포인트
GACHA_COST = 20                # 뽑기 1회 소모 포인트
