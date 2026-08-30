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
ARCHIVE_CATEGORY_NAME = "티켓 아카이브" # 티켓 보관함 카테고리 이름
LOG_CHANNEL_NAME = "로그"             # 일반 로그 채널 이름

# ==========================================
# 2. 주요 채널 ID 설정
# ==========================================
# [보안 및 관리 채널]
SECURITY_LOG_CHANNEL_ID = 1505122707098828806   # dds 보안실 채널 (보안 경고 및 PII 차단 로그 수신)

# [커미션 & 안내 관련 채널]
PURCHASE_CHANNEL_ID = 1505102694917079132        # 구매/문의 채널
INQUIRIES_CHANNEL_ID = 1505102694917079132       # dial.py 인식용 문의/티켓 채널 ID (PURCHASE_CHANNEL_ID와 동일)
EXAMPLE_CHANNEL_ID = 1505178799950532720         # 예시작 채널
REVIEWS_CHANNEL_ID = 1506517440463638581         # 후기 채널
SALE_NOTICE_CHANNEL_ID = 1505562851824369714     # 구매 알림 채널
DESIGNER_STATS_CHANNEL_ID = 1521001578239361155  # 디자이너 통계 채널
DESIGNER_TIER_CHANNEL_ID = 1537806140711239760   # 디자이너 등급 채널

# [포인트 & 활동 관련 채널]
POINT_RANKING_CHANNEL_ID = 1532599012316938321   # 포인트 랭킹 채널
POINT_INFO_CHANNEL_ID = 1532373833783316610      # 포인트 적립/안내 채널
COMMAND_CHANNEL_ID = 1531287070281040054         # 명령어/미니게임 전용 채널

# [2일 주기 / 오후 6시 자동 가이드 채팅 채널]
KR_CHAT_CHANNEL_ID = 1505074223356317771         # 한국어 채팅 채널
EN_CHAT_CHANNEL_ID = 1527725232864100362         # 영어 채팅 채널

# PII 감지 예외 채널 목록 (검열에서 아예 제외할 채널 ID)
EXCLUDED_PII_CHANNELS = [
    # 필요한 경우 검열을 제외할 채널 ID를 추가하세요.
]

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

GACHA_COST = 20                # 뽑기 1회 소모 포인트

# ==========================================
# 5. 자동 가이드 메시지 템플릿 (2일 주기 / 오후 6시)
# ==========================================

# 한국어 채널용 가이드
GUIDE_MESSAGE_KR = f"""✨ DDS (Design & Developer Service) 안내 가이드

DDS 공식 서버에 오신 것을 환영합니다! 🎉
원활한 서버 이용을 위한 주요 채널 안내입니다.

📌 핵심 채널
• <#{PURCHASE_CHANNEL_ID}> : 커미션 신청 및 파트너/개발 문의
• 🗣️ | 📸 | <#{EXAMPLE_CHANNEL_ID}> : 디자이너 포트폴리오 및 샘플
• 📢 | 📊 | <#{DESIGNER_TIER_CHANNEL_ID}> : 디자이너 등급 및 분야
• 📢 | 📊 | <#{DESIGNER_STATS_CHANNEL_ID}> : 디자이너 작업 완료 통계
• # | 😊 | <#{REVIEWS_CHANNEL_ID}> : 솔직한 이용 후기 및 피드백

🪙 포인트 & 혜택
• # | 🏆 | <#{POINT_RANKING_CHANNEL_ID}> : 실시간 포인트 순위
• 📢 | 🔍 | <#{POINT_INFO_CHANNEL_ID}> : 포인트 적립 방법 및 단골 혜택 ({int(REGULAR_DISCOUNT_RATE * 100)}% 할인)

자동 가이드 안내 | 2일 주기 (오후 6시)"""

# 영어 채널용 가이드
GUIDE_MESSAGE_EN = f"""✨ DDS (Design & Developer Service) Guide

Welcome to DDS Official Server! 🎉
Here is a quick directory of our main channels to help you get started.

📌 Essential Channels
• <#{PURCHASE_CHANNEL_ID}> : Order commissions & Partner/Dev inquiries
• 🗣️ | 📸 | <#{EXAMPLE_CHANNEL_ID}> : Designer portfolio & sample showcase
• 📢 | 📊 | <#{DESIGNER_TIER_CHANNEL_ID}> : Designer ranks & categories
• 📢 | 📊 | <#{DESIGNER_STATS_CHANNEL_ID}> : Designer completed work statistics
• # | 😊 | <#{REVIEWS_CHANNEL_ID}> : Genuine customer reviews & feedback

🪙 Points & Rewards
• # | 🏆 | <#{POINT_RANKING_CHANNEL_ID}> : Real-time Point Leaderboard
• 📢 | 🔍 | <#{POINT_INFO_CHANNEL_ID}> : How to earn points & VIP perks ({int(REGULAR_DISCOUNT_RATE * 100)}% OFF)

Auto Guide Notice | Every 2 Days at 6:00 PM"""
