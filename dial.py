import discord
import asyncio
import os
import re
import aiosqlite
from datetime import datetime, timedelta
from discord.ext import commands
from database.database import DATABASE, create_tables
from config import *
from database.views.ticket_view import TicketOpenView
from database.views.close_ticket import (
    TicketCloseView,
    archive_ticket_channel,
    delete_ticket_dm_messages,
    has_designer_role,
)
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
persistent_views_registered = False
PROCESSED_TABLES = {
    "processed_commands",
    "processed_command_errors",
}


async def claim_once(table_name, message_id):
    if table_name not in PROCESSED_TABLES:
        raise ValueError("허용되지 않은 처리 기록 테이블입니다.")

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


def parse_mention_id(text):
    match = re.search(r"<@!?(\d+)>", text or "")
    return int(match.group(1)) if match else None


def is_ticket_channel(channel):
    return (
        isinstance(channel, discord.TextChannel)
        and channel.name.startswith("티켓-")
    )


async def find_ticket_owner(channel):
    try:
        if channel.topic:
            return channel.guild.get_member(int(channel.topic))
    except (TypeError, ValueError):
        pass

    async for msg in channel.history(limit=5, oldest_first=True):
        if msg.mentions:
            return msg.mentions[0]

    return None


async def find_ticket_designer_id(channel):
    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            for field in embed.fields:
                if field.name == "👨‍💻 담당 디자이너":
                    designer_id = parse_mention_id(field.value)

                    if designer_id:
                        return designer_id

            designer_id = parse_mention_id(embed.description)

            if designer_id:
                return designer_id

    return None


def can_manage_ticket(member, user_id, designer_id):
    if member is None:
        return False

    if member.guild_permissions.administrator:
        return True

    if designer_id is not None:
        return user_id == designer_id

    return has_designer_role(member)


async def fetch_member_or_none(guild, member_id):
    if not member_id:
        return None

    member = guild.get_member(member_id)

    if member:
        return member

    try:
        return await guild.fetch_member(member_id)
    except Exception:
        return None


async def build_ticket_summary(channel):
    message_count = 0
    attachment_count = 0
    participants = set()

    async for msg in channel.history(limit=100, oldest_first=False):
        if msg.author.bot:
            continue

        message_count += 1
        participants.add(msg.author.display_name)
        attachment_count += len(msg.attachments)

    created_at = channel.created_at
    closed_at = datetime.now(created_at.tzinfo)
    total_minutes = int((closed_at - created_at).total_seconds() // 60)

    return {
        "message_count": message_count,
        "attachment_count": attachment_count,
        "participants": ", ".join(sorted(participants)) or "없음",
        "hours": total_minutes // 60,
        "minutes": total_minutes % 60,
    }


async def send_payment_info(channel, designer_id):
    async with aiosqlite.connect(DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT bank_name, account_number, holder
            FROM bank_accounts
            WHERE developer_id = ?
            """,
            (designer_id,)
        )

        data = await cursor.fetchone()

    if data is None:
        return False

    bank_name, account_number, holder = data

    embed = discord.Embed(
        title="💳 결제 정보",
        description=(
            f"🏦 {bank_name}\n"
            f"계좌번호 : `{account_number}`\n"
            f"예금주 : **{holder}**\n\n"
            "✅ 입금 후 담당 디자이너에게 말씀해주세요."
        ),
        color=discord.Color.green()
    )

    await channel.send(embed=embed)
    return True

# ==================== [티켓 패널 명령어] ====================

@bot.check
async def prevent_duplicate_command_processing(ctx):
    return await claim_once("processed_commands", ctx.message.id)
    
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


@bot.command(name="계좌전송", aliases=["계좌번호", "계좌번호전송", "결제정보", "결제"])
async def send_bank_to_ticket(ctx, member: discord.Member = None):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    author = ctx.guild.get_member(ctx.author.id)
    is_admin = author and author.guild_permissions.administrator
    designer_id = member.id if member else await find_ticket_designer_id(ctx.channel)

    if designer_id is None and has_designer_role(author):
        designer_id = ctx.author.id

    if designer_id is None:
        return await ctx.send(
            "❌ 담당 디자이너를 찾지 못했습니다. 관리자라면 `!계좌전송 @디자이너`로 사용해주세요."
        )

    if not is_admin and ctx.author.id != designer_id:
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 계좌를 전송할 수 있습니다.")

    if not await send_payment_info(ctx.channel, designer_id):
        return await ctx.send("❌ 담당 디자이너의 계좌가 등록되어 있지 않습니다.")

    await ctx.reply(
        "✅ 결제 정보를 티켓에 전송했습니다.",
        mention_author=False,
        delete_after=3
    )


@bot.command(name="티켓닫기", aliases=["티켓종료", "닫기"])
async def close_ticket_by_command(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    closer = guild.get_member(ctx.author.id)

    if not can_manage_ticket(closer, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 티켓을 종료할 수 있습니다.")

    notice = await ctx.send("🔒 티켓 종료 처리 중입니다.")
    ticket_owner = await find_ticket_owner(channel)
    designer = await fetch_member_or_none(guild, designer_id)

    if designer:
        await delete_ticket_dm_messages(bot.user, designer, channel)

    summary = await build_ticket_summary(channel)

    log_channel = discord.utils.get(
        guild.text_channels,
        name=LOG_CHANNEL_NAME
    )

    if log_channel:
        log_embed = discord.Embed(
            title="🧾 구매 / 상담 로그",
            color=0x5865F2,
            timestamp=datetime.now()
        )

        log_embed.add_field(
            name="👤 고객",
            value=ticket_owner.mention if ticket_owner else "알 수 없음",
            inline=True
        )
        log_embed.add_field(
            name="🔒 종료자",
            value=ctx.author.mention,
            inline=True
        )
        log_embed.add_field(
            name="💬 메시지 수",
            value=str(summary["message_count"]),
            inline=True
        )
        log_embed.add_field(
            name="⏱ 상담 시간",
            value=f"{summary['hours']}시간 {summary['minutes']}분",
            inline=True
        )
        log_embed.add_field(
            name="📎 첨부파일",
            value=f"{summary['attachment_count']}개",
            inline=True
        )
        log_embed.add_field(
            name="👥 참여자",
            value=summary["participants"],
            inline=False
        )
        log_embed.set_footer(text="개인정보는 저장되지 않았습니다.")

        await log_channel.send(
            content=(
                f"🔒 {ticket_owner.mention} 님의 티켓이 종료되었습니다."
                if ticket_owner
                else "🔒 티켓이 종료되었습니다."
            ),
            embed=log_embed
        )

    try:
        buyer_role = guild.get_role(BUYER_ROLE_ID)

        if ticket_owner and buyer_role and buyer_role not in ticket_owner.roles:
            await ticket_owner.add_roles(
                buyer_role,
                reason="커미션 완료 자동 구매자 역할 지급"
            )
    except Exception as role_err:
        print(f"[구매자 역할 지급 실패] {role_err}")

    archive_notice = discord.Embed(
        title="🔒 티켓이 종료되었습니다",
        description="상담 기록 보관을 위해 이 채널은 잠시 후 아카이브로 이동합니다.",
        color=discord.Color.dark_grey(),
        timestamp=datetime.now()
    )

    archive_notice.add_field(
        name="처리 내용",
        value=(
            "• 구매/상담 로그 저장\n"
            "• 디자이너 관리 DM 정리\n"
            "• 채널 보관함 이동"
        ),
        inline=False
    )

    await notice.edit(content="✅ 티켓 종료 처리 완료. 곧 보관함으로 이동합니다.")
    await channel.send(embed=archive_notice)
    await asyncio.sleep(5)
    await archive_ticket_channel(channel)

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
    if amount < 1 or amount > 100:
        return await ctx.send("사용법: `!청소 1~100`")

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

    if (
        isinstance(error, commands.CheckFailure)
        and not isinstance(
            error,
            (
                commands.MissingPermissions,
                commands.BotMissingPermissions,
            )
        )
    ):
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
                "`!완료`\n"
                "`!계좌전송`\n"
                "`!티켓닫기`"
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
    global daily_notice, persistent_views_registered

    await create_tables()

    print("on_ready")

    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")

    if not persistent_views_registered:
        bot.add_view(TicketOpenView())
        bot.add_view(StarRatingView())
        # bot.add_view(ProgressView())
        bot.add_view(VerifyView())
        persistent_views_registered = True

    if daily_notice is None:
        print("DailyNotice 생성 전")
        daily_notice = DailyNotice(bot)
        print("DailyNotice 생성 완료")

    print("✨ 영속성 버튼 등록 완료!")

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
