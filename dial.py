import discord
import os
import re
import aiosqlite
from datetime import datetime, timedelta
from discord.ext import commands
from database.database import DATABASE, create_tables
from config import *
from database.views.ticket_view import TicketOpenView
from database.views.close_ticket import TicketCloseView
from database.views.review_view import StarRatingView
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView
from database.DailyNotice import DailyNotice
from database.views.verify_view import VerifyView

TOKEN = os.getenv("TOKEN")

# 알림을 받을 개발자(관리자)들의 디스코드 고유 ID 리스트

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
daily_notice = None


async def claim_once(table_name, message_id):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            f"INSERT OR IGNORE INTO {table_name}(message_id) VALUES (?)",
            (message_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


@bot.check
async def prevent_duplicate_command_processing(ctx):
    return await claim_once("processed_commands", ctx.message.id)


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


def mask_account(account_number):
    digits = re.sub(r"\D", "", account_number)

    if len(digits) <= 4:
        return "****"

    return f"{digits[:3]}****{digits[-4:]}"

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
async def register_bank(
    ctx,
    member: discord.Member,
    bank_name,
    account_number,
    holder
):

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
                member.id,
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
        name="대상 디자이너",
        value=member.mention,
        inline=False
    )

    embed.add_field(
        name="은행",
        value=bank_name,
        inline=False
    )

    embed.add_field(
        name="계좌번호",
        value=f"`{mask_account(account_number)}`",
        inline=False
    )

    embed.add_field(
        name="예금주",
        value=holder,
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="계좌삭제")
@commands.has_permissions(administrator=True)
async def delete_bank(ctx):

    async with aiosqlite.connect("data/dialian.db") as db:

        await db.execute(
            """
            DELETE FROM bank_accounts
            WHERE developer_id = ?
            """,
            (ctx.author.id,)
        )

        await db.commit()

    await ctx.send("✅ 등록된 계좌가 삭제되었습니다.")

@bot.command(name="계좌목록")
@commands.has_permissions(administrator=True)
async def bank_list(ctx):

    async with aiosqlite.connect("data/dialian.db") as db:

        cursor = await db.execute(
            """
            SELECT
                developer_id,
                bank_name,
                account_number,
                holder
            FROM bank_accounts
            ORDER BY developer_id
            """
        )

        rows = await cursor.fetchall()

    if not rows:
        return await ctx.send("❌ 등록된 계좌가 없습니다.")

    embed = discord.Embed(
        title="💳 디자이너 계좌 목록",
        color=discord.Color.blurple()
    )

    for developer_id, bank, account, holder in rows:

        member = ctx.guild.get_member(developer_id)

        name = member.mention if member else f"`{developer_id}`"

        embed.add_field(
            name=name,
            value=(
                f"🏦 **은행** : {bank}\n"
                f"💳 **계좌** : `{mask_account(account)}`\n"
                f"👤 **예금주** : {holder}"
            ),
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

        if not embed.description:
            continue

        lines = embed.description.splitlines()

        if len(lines) < 4:
            return await ctx.send("진행 패널 형식이 올바르지 않습니다.")

        designer = lines[0]
        estimate = lines[3]

        embed.description = (
            f"{designer}\n\n"
            f"📌 상태 : {status}\n"
            f"📊 진행률 : {percent}%\n"
            f"{estimate}"
        )

        await msg.edit(embed=embed)
        await ctx.send("✅ 진행률이 변경되었습니다.", delete_after=3)
        return

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

        if not embed.description:
            continue

        lines = embed.description.splitlines()

        if len(lines) < 4:
            return await ctx.send("진행 패널 형식이 올바르지 않습니다.")

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
        await ctx.send("✅ 예상 작업일이 변경되었습니다.", delete_after=3)
        return

    await ctx.send("진행 패널을 찾지 못했습니다.")

@bot.command(name="청소")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):

    await ctx.channel.purge(limit=amount + 1)

    msg = await ctx.send(f"✅ {amount}개의 메시지를 삭제했습니다.")

    await msg.delete(delay=3)

@bot.command(name="인증패널")
@commands.has_permissions(administrator=True)
async def verify_panel(ctx):

    embed = discord.Embed(
        title="✅ 서버 인증",
        description="아래 버튼을 눌러 인증을 완료해주세요.",
        color=discord.Color.green()
    )

    await ctx.send(
        embed=embed,
        view=VerifyView()
    )


@bot.command(name="dm테스트")
@commands.has_permissions(administrator=True)
async def dm_test(ctx, member: discord.Member):

    await ctx.send(
        f"🔎 DM 테스트 시작\n"
        f"대상: {member.mention}\n"
        f"ID: `{member.id}`\n"
        f"서버 멤버 여부: `{member.guild.id == ctx.guild.id}`"
    )

    try:
        dm_channel = await member.create_dm()
        await dm_channel.send("✅ Dialian 봇 DM 테스트입니다.")

    except discord.Forbidden as e:
        return await ctx.send(
            "❌ DM 전송 실패: Discord가 이 유저에게 DM을 막았습니다.\n"
            f"에러 코드: `{getattr(e, 'code', 'unknown')}`\n"
            f"원문: `{e}`\n\n"
            "부계에서 해당 서버의 DM 허용 설정, 봇 차단 여부, "
            "개인정보 설정을 확인해야 합니다."
        )

    except Exception as e:
        return await ctx.send(
            "❌ DM 전송 중 예외가 발생했습니다.\n"
            f"`{type(e).__name__}: {e}`"
        )

    await ctx.send("✅ DM 전송 성공")


@bot.command(name="완료")
@commands.has_permissions(administrator=True)
async def complete(ctx):

    designer_id = None

    async for msg in ctx.channel.history(limit=30):

        if msg.author != bot.user:
            continue

        if not msg.embeds:
            continue

        embed = msg.embeds[0]

        if embed.title == "📌 커미션 진행":

            if not embed.description:
                continue

            lines = embed.description.splitlines()

            if not lines:
                return await ctx.send("진행 패널 형식이 올바르지 않습니다.")

            designer = lines[0]
            designer_match = re.search(r"<@!?(\d+)>", designer)

            if designer_match:
                designer_id = int(designer_match.group(1))

            embed.description = (
                f"{designer}\n\n"
                "📌 상태 : ✅ 완료\n"
                "📊 진행률 : 100%\n"
                "⏰ 예상 완료 : 완료"
            )

            await msg.edit(embed=embed)

            break

    review_embed = discord.Embed(
        title="⭐ 작업이 완료되었습니다!",
        description="아래 버튼을 눌러 만족도를 평가해주세요.",
        color=discord.Color.gold()
    )

    await ctx.send(
        embed=review_embed,
        view=StarRatingView(designer_id)
    )

    try:
        channel_name = ctx.channel.name

        if ctx.channel.topic:
            member = ctx.guild.get_member(int(ctx.channel.topic))
        elif channel_name.startswith("티켓-"):
            nickname = channel_name.replace("티켓-", "")

            member = discord.utils.find(
                lambda m: m.display_name.lower().replace(" ", "-") == nickname,
                ctx.guild.members
            )
        else:
            member = None

        if member:
            role = ctx.guild.get_role(BUYER_ROLE_ID)

            if role and role not in member.roles:
                await member.add_roles(role)

    except Exception as e:
        print(e)


async def send_private_command_notice(ctx, title, description):
    try:
        if ctx.guild:
            permissions = ctx.channel.permissions_for(ctx.guild.me)

            if permissions.manage_messages:
                await ctx.message.delete()

    except Exception:
        pass

    message = f"{title}\n\n{description}"

    try:
        await ctx.author.send(message)
        return

    except discord.Forbidden:
        pass

    try:
        await ctx.reply(
            "명령어 입력이 올바르지 않습니다. DM을 보낼 수 없어 여기서 잠시 안내합니다.",
            mention_author=False,
            delete_after=8
        )

    except Exception:
        pass


def get_command_usage(ctx):
    if ctx.command is None:
        return None

    signature = ctx.command.signature
    usage = f"!{ctx.command.name} {signature}".strip()
    return usage


@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)

    if isinstance(error, commands.CheckFailure):
        return

    if isinstance(error, commands.CommandNotFound):
        if not await claim_once("processed_command_errors", ctx.message.id):
            return

        return await send_private_command_notice(
            ctx,
            "❌ 존재하지 않는 명령어입니다.",
            (
                "명령어를 다시 확인해주세요.\n\n"
                "자주 쓰는 명령어:\n"
                "`!티켓생성`\n"
                "`!인증패널`\n"
                "`!진행 0|25|50|75|100`\n"
                "`!예상 1일|2일|3일`\n"
                "`!완료`"
            )
        )

    if isinstance(error, commands.MissingRequiredArgument):
        usage = get_command_usage(ctx)

        return await send_private_command_notice(
            ctx,
            "❌ 명령어 입력값이 부족합니다.",
            f"아래 형식으로 다시 입력해주세요.\n`{usage}`"
        )

    if isinstance(error, commands.BadArgument):
        usage = get_command_usage(ctx)

        return await send_private_command_notice(
            ctx,
            "❌ 명령어 입력 형식이 올바르지 않습니다.",
            f"멘션, 숫자, 날짜 형식을 다시 확인해주세요.\n`{usage}`"
        )

    if isinstance(error, commands.MissingPermissions):
        return await send_private_command_notice(
            ctx,
            "❌ 권한이 부족합니다.",
            "이 명령어를 사용할 권한이 없습니다."
        )

    if isinstance(error, commands.BotMissingPermissions):
        return await send_private_command_notice(
            ctx,
            "❌ 봇 권한이 부족합니다.",
            "봇 역할 권한을 확인해주세요."
        )

    print(f"[명령어 에러] {ctx.command}: {error}")
    await send_private_command_notice(
        ctx,
        "❌ 명령어 처리 중 오류가 발생했습니다.",
        "입력 내용을 확인한 뒤 다시 시도해주세요."
    )


# ==================== [봇 시작 시스템] ====================

@bot.event
async def on_ready():
    global daily_notice

    await create_tables()

    print("on_ready")

    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")

    bot.add_view(TicketOpenView())
    bot.add_view(StarRatingView())
    # bot.add_view(ProgressView())
    bot.add_view(VerifyView())

    if daily_notice is None:
        print("DailyNotice 생성 전")
        daily_notice = DailyNotice(bot)
        print("DailyNotice 생성 완료")

    print("✨ 영속성 버튼 등록 완료!")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
