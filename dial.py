import discord
import os
import re
import aiosqlite
from datetime import datetime, timedelta
from discord.ext import commands
from database.database import create_tables
from views.ticket_view import TicketOpenView
from config import *
from views.close_ticket import TicketCloseView
from views.review_view import StarRatingView
from views.progress_view import ProgressView
from views.payment_view import PaymentView

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

@bot.command(name="통계")
@commands.has_permissions(administrator=True)
async def stats(ctx):

    since = (datetime.now() - timedelta(days=14)).isoformat()

    async with aiosqlite.connect("data/dialian.db") as db:

        cursor = await db.execute(
            """
            SELECT
                COUNT(*),
                AVG(stars)
            FROM reviews
            WHERE created_at >= ?
            """,
            (since,)
        )

        total_reviews, avg_rating = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT
                developer_id,
                COUNT(*),
                AVG(stars)
            FROM reviews
            WHERE created_at >= ?
            GROUP BY developer_id
            ORDER BY COUNT(*) DESC
            """,
            (since,)
        )

        developers = await cursor.fetchall()

    embed = discord.Embed(
        title="📊 최근 2주 통계",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="⭐ 전체 후기",
        value=total_reviews or 0,
        inline=True
    )

    embed.add_field(
        name="⭐ 평균 평점",
        value=f"{(avg_rating or 0):.2f}/5",
        inline=True
    )

    if developers:

        text = ""

        for dev_id, count, avg in developers:
            member = ctx.guild.get_member(dev_id)

            name = member.mention if member else str(dev_id)

            text += (
                f"{name}\n"
                f"후기 {count}개 | 평균 {(avg or 0):.2f}⭐\n\n"
            )

        embed.add_field(
            name="👨‍💻 디자이너 통계",
            value=text,
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name="계좌등록")
@commands.has_permissions(administrator=True)
async def register_bank(ctx, bank_name, account_number, holder):

    async with aiosqlite.connect("data/dialian.db") as db:

        await db.execute(
            """
            INSERT OR REPLACE INTO bank_accounts(
                developer_id,
                bank_name,
                account_number,
                holder
            )
            VALUES(?,?,?,?)
            """,
            (
                ctx.author.id,
                bank_name,
                account_number,
                holder
            )
        )

        await db.commit()

    embed = discord.Embed(
        title="✅ 계좌 등록 완료",
        color=discord.Color.green()
    )

    embed.add_field(
        name="은행",
        value=bank_name,
        inline=False
    )

    embed.add_field(
        name="계좌번호",
        value=f"`{account_number}`",
        inline=False
    )

    embed.add_field(
        name="예금주",
        value=holder,
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command(name="진행")
@commands.has_permissions(administrator=True)
async def progress(ctx, percent: int):

    if percent not in [0, 25, 50, 75, 100]:
        return await ctx.send("사용법: `!진행 0|25|50|75|100`")

    status = {
        0: "🟢 상담중",
        25: "🟡 작업 시작",
        50: "🟠 작업중",
        75: "🔵 마무리 작업",
        100: "✅ 완료"
    }[percent]

    async for msg in ctx.channel.history(limit=30):

        if msg.author != bot.user:
            continue

        if not msg.embeds:
            continue

        embed = msg.embeds[0]

        if embed.title != "📌 커미션 진행":
            continue

        lines = embed.description.split("\n")

        designer = lines[0]
        estimate = lines[3]

        embed.description = (
            f"{designer}\n\n"
            f"📌 상태 : {status}\n"
            f"📊 진행률 : {percent}%\n"
            f"{estimate}"
        )

        await msg.edit(embed=embed)
        return await ctx.message.add_reaction("✅")

    await ctx.send("진행 패널을 찾지 못했습니다.")

@bot.command(name="예상")
@commands.has_permissions(administrator=True)
async def estimate(ctx, days: str):

    if days not in ["1일", "2일", "3일"]:
        return await ctx.send("사용법: `!예상 1일|2일|3일`")

    async for msg in ctx.channel.history(limit=30):

        if msg.author != bot.user:
            continue

        if not msg.embeds:
            continue

        embed = msg.embeds[0]

        if embed.title != "📌 커미션 진행":
            continue

        lines = embed.description.split("\n")

        designer = lines[0]
        status = lines[2]
        progress = lines[3]

        embed.description = (
            f"{designer}\n\n"
            f"{status}\n"
            f"{progress}\n"
            f"⏰ 예상 완료 : {days}"
        )

        await msg.edit(embed=embed)
        return await ctx.message.add_reaction("✅")

    await ctx.send("진행 패널을 찾지 못했습니다.")

@bot.command(name="완료")
@commands.has_permissions(administrator=True)
async def complete(ctx):

    async for msg in ctx.channel.history(limit=30):

        if msg.author != bot.user:
            continue

        if not msg.embeds:
            continue

        embed = msg.embeds[0]

        if embed.title == "📌 커미션 진행":

            lines = embed.description.split("\n")

            designer = lines[0]

            embed.description = (
                f"{designer}\n\n"
                "📌 상태 : ✅ 완료\n"
                "📊 진행률 : 100%\n"
                "⏰ 예상 완료 : 완료"
            )

            await msg.edit(embed=embed)

            break

    from views.review_view import StarRatingView

    review_embed = discord.Embed(
        title="⭐ 작업이 완료되었습니다!",
        description="아래 버튼을 눌러 만족도를 평가해주세요.",
        color=discord.Color.gold()
    )

    await ctx.send(
        embed=review_embed,
        view=StarRatingView()
    )

    try:
        channel_name = ctx.channel.name

 if channel_name.startswith("티켓-"):
        nickname = channel_name.replace("티켓-", "")

        member = discord.utils.find(
            lambda m: m.display_name.lower().replace(" ", "-") == nickname,
            ctx.guild.members
        )

        if member:
            role = ctx.guild.get_role(BUYER_ROLE_ID)

            if role and role not in member.roles:
                await member.add_roles(role)

except Exception as e:
    print(e)


# ==================== [봇 시작 시스템] ====================

@bot.event
async def on_ready():
    
    await create_tables()
    
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")
    print("--------------------------------------------------")

    bot.add_view(TicketOpenView())
    bot.add_view(StarRatingView())
    bot.add_view(TicketCloseView())
    bot.add_view(ProgressView())
    bot.add_view(PaymentView(0))
    

    

    print("✨ 영속성 버튼 등록 완료!")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
