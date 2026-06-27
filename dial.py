import discord
import os
import re
from discord.ext import commands
from database.database import create_tables
from views.ticket_view import TicketOpenView
from config import *
from views.close_ticket import TicketCloseView
from views.review_view import StarRatingView

TOKEN = os.getenv("TOKEN")

# 알림을 받을 개발자(관리자)들의 디스코드 고유 ID 리스트

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== [보안용 텍스트 정리 함수] ====================

def sanitize_text(text):
    if not text:
        return "[내용 없음]"

    # URL 제거
    text = re.sub(r'https?://\S+', '[LINK]', text)

    # 디스코드 초대링크 제거
    text = re.sub(r'discord\.gg/\S+', '[INVITE]', text)

    # 이메일 제거
    text = re.sub(r'\S+@\S+', '[EMAIL]', text)

    # 전화번호 제거
    text = re.sub(r'\d{2,3}-\d{3,4}-\d{4}', '[PHONE]', text)

    # 긴 숫자 제거 (계좌번호/주문번호 등)
    text = re.sub(r'\d{6,}', '[NUMBER]', text)

    return text[:80]

# ==================== [티켓 패널 명령어] ====================

@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):

    file = discord.File("price.png", filename="price.png")

    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description=(
            "상담, 구매 진행, 문의사항이 있으시다면\n"
            "아래 📩 버튼을 눌러주세요!\n\n"
            "📌 구매 전 가격표를 확인해주세요."
        ),
        color=0x5865F2
    )

    embed.set_image(url="attachment://price.png")

    await ctx.send(
        file=file,
        embed=embed,
        view=TicketOpenView()
    )

# ==================== [봇 시작 시스템] ====================

@bot.event
async def on_ready():
    
    await create_tables()
    
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")
    print("--------------------------------------------------")

    bot.add_view(TicketOpenView())
    bot.add_view(StarRatingView())
    bot.add_view(TicketCloseView())

    

    print("✨ 영속성 버튼 등록 완료!")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
