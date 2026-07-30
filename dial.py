import asyncio
import os
import random
import re
import subprocess
from datetime import datetime

import aiosqlite
import discord
from discord.ext import commands, tasks

from config import *
from database.backups import backup_database
from database.database import DATABASE, create_tables
from database.DailyNotice import DailyNotice
from database.monthly_stats import (
    build_monthly_stats_embed,
    save_monthly_stats_message,
    update_monthly_stats_message,
)
from database.services.points import (
    add_user_points,
    check_and_add_feedback_points,
    check_and_add_share_points,
    get_user_points,
)
from database.views.category_view import CategoryView
from database.views.close_ticket import (
    TicketCloseView,
    archive_ticket_channel,
    delete_all_bot_dm_messages,
    delete_ticket_channel,
    delete_ticket_dm_messages,
    has_designer_role,
)
from database.views.payment_view import PaymentView
from database.views.progress_view import ProgressView
from database.views.review_view import StarRatingView
from database.views.ticket_view import TicketOpenView
from database.views.verify_view import VerifyView

TOKEN = os.getenv("TOKEN")


def get_bot_version():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

daily_notice = None
persistent_views_registered = False
update_notice_sent = False
bot_started_at = datetime.now()

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


# ==================== [채널 유효성 검사 헬퍼] ====================

async def check_command_channel(ctx):
    """명령어 전용 채널인지 확인합니다."""
    if ctx.channel.id != COMMAND_CHANNEL_ID:
        await ctx.send(
            f"❌ 해당 명령어는 <#{COMMAND_CHANNEL_ID}> 채널에서만 사용할 수 있습니다.",
            delete_after=5
        )
        return False
    return True


# ==================== [일일 활동 횟수 제한 로직] ====================

async def check_and_increment_daily_limit(user_id: int, action_type: str, max_limit: int = DAILY_ACTION_LIMIT):
    """하루 최대 제한 횟수를 체크하고 카운트를 증가시킵니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_activity_limits (
                user_id INTEGER,
                action_type TEXT,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, action_type, date)
            )
        """)
        cursor = await db.execute("""
            SELECT count FROM daily_activity_limits
            WHERE user_id = ? AND action_type = ? AND date = ?
        """, (user_id, action_type, today))
        row = await cursor.fetchone()
        current_count = row[0] if row else 0

        if current_count >= max_limit:
            return False, current_count

        await db.execute("""
            INSERT INTO daily_activity_limits (user_id, action_type, date, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, action_type, date) DO UPDATE SET count = count + 1
        """, (user_id, action_type, today))
        await db.commit()
        return True, current_count + 1


# ==================== [포인트 자동 감지 이벤트] ====================

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # 작품공유 채널 메시지 감지 (+15P, 1일 최대 3회)
    if hasattr(message.channel, "id") and message.channel.id == WORK_SHARE_CHANNEL_ID:
        can_earn, count = await check_and_increment_daily_limit(message.author.id, "work_share")
        if can_earn:
            success = await check_and_add_share_points(message.guild, message.author, message)
            if success:
                try:
                    await message.add_reaction("🪙")
                except Exception:
                    pass

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload):
    if not payload.guild_id or payload.user_id == bot.user.id:
        return

    # 피드백 채널 감지 (+10P, 모든 반응 허용, 1일 최대 3회)
    if payload.channel_id != FEEDBACK_CHANNEL_ID:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    channel = guild.get_channel(payload.channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    # 피드백 작성자 본인이 단 반응이거나 봇 메시지면 무시
    if message.author.id == payload.user_id or message.author.bot:
        return

    can_earn, count = await check_and_increment_daily_limit(payload.user_id, "feedback_react")
    if can_earn:
        user = guild.get_member(payload.user_id)
        if user:
            success = await check_and_add_feedback_points(guild, user, message)
            if success:
                try:
                    await message.add_reaction("🪙")
                except Exception:
                    pass


# ==================== [기본 / 포인트 / 명예 / 미니게임 명령어] ====================

@bot.command(name="명령어", aliases=["help", "도움말"])
async def command_list(ctx):
    if not await check_command_channel(ctx):
        return

    embed = discord.Embed(
        title="Dialian 명령어 목록",
        description=(
            "**[티켓 및 일반 서비스]**\n"
            "`!티켓생성` `!계좌전송` `!티켓닫기` `!티켓삭제` `!인증패널`\n"
            "`!진행 0|25|50|75|100` `!예상 1일|2일|3일` `!완료` `!청소 1~100`\n"
            "`!계좌등록` `!계좌목록` `!계좌삭제` `!통계` `!통계동기화`\n\n"
            "**[포인트 & 프로필]** *(명령어 채널 전용)*\n"
            "`!포인트` `!포인트지급 @유저 금액` `!포인트차감 @유저 금액` `!포인트리셋 @유저`\n\n"
            "**[🎰 오락실 & 미니게임]** *(명령어 채널 전용)*\n"
            "`!뽑기` - 20P 소모 (꽝 확률 조정형)\n"
            "`!가위바위보 [가위/바위/보] [배팅포인트]` - 승리 시 2배! (패배 시 5% 위로포인트)\n"
            "`!묵찌빠 [가위/바위/보] [배팅포인트]` - 승리 시 2.5배! (패배 시 5% 위로포인트)\n\n"
            "**[명예 및 베스트]**\n"
            "`!주간베스트` (명예의 전당 집계)"
        ),
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


@bot.command(name="포인트", aliases=["마일리지", "p"])
async def show_points(ctx, member: discord.Member = None):
    if not await check_command_channel(ctx):
        return

    target = member or ctx.author
    points = await get_user_points(target.id)

    if points >= 500:
        tier_icon = "🥇"
        tier_name = "골드 (최상위 VVIP 단골)"
        color = discord.Color.gold()
    elif points >= 300:
        tier_icon = "🥈"
        tier_name = "실버 (단골 유망주)"
        color = discord.Color.light_grey()
    elif points >= 100:
        tier_icon = "🥉"
        tier_name = "브론즈"
        color = discord.Color.dark_orange()
    else:
        tier_icon = "🌱"
        tier_name = "뉴비"
        color = discord.Color.green()

    embed = discord.Embed(
        title=f"📊 {target.display_name} 님의 프로필",
        color=color
    )
    
    embed.add_field(
        name="현재 계급 (티어)",
        value=f"{tier_icon} **{tier_name}**",
        inline=False
    )
    
    embed.add_field(
        name="현재 포인트",
        value=f"`{points} P` / (골드 기준: `500 P`)",
        inline=False
    )

    if points >= 500:
        embed.add_field(
            name="🎁 해제된 최고 혜택",
            value="✅ **골드 단골 손님 (모든 커미션 15% 자동 할인 적용 중)**",
            inline=False
        )
    else:
        remaining = 500 - points
        embed.add_field(
            name="승급까지 남은 길",
            value=f"최고 등급 **골드(단골 15% 할인)**까지 **{remaining} P** 남았습니다!",
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command(name="뽑기", aliases=["가챠", "럭키드로우"])
async def point_gacha(ctx):
    if not await check_command_channel(ctx):
        return

    current_points = await get_user_points(ctx.author.id)
    cost = GACHA_COST
    
    if current_points < cost:
        return await ctx.send(f"❌ 포인트가 부족합니다. (현재 `{current_points}P` / 필요 `{cost}P`)")
    
    await add_user_points(ctx.guild, ctx.author, -cost)
    
    # [수정된 밸런스] 꽝(5P) 확률 70%로 상향, 본전(20P) 15%로 하향
    prizes = [5, 20, 35, 75, 150, 400]
    weights = [70, 15, 9, 4.5, 1.3, 0.2]
    result = random.choices(prizes, weights=weights, k=1)[0]
    
    await add_user_points(ctx.guild, ctx.author, result)
    final_points = await get_user_points(ctx.author.id)
    
    if result == 5:
        color = discord.Color.dark_grey()
        title = "😭 아쉽게 꽝!"
        desc = "하지만 마음을 달래줄 **위로 포인트 5P**를 받으셨습니다!"
    elif result == 20:
        color = discord.Color.light_grey()
        title = "😐 본전치기!"
        desc = "소모한 20P를 그대로 찾아왔습니다."
    elif result == 400:
        color = discord.Color.magenta()
        title = "🔥 전설의 400P 잭팟 터짐!!!"
        desc = f"0.2%의 확률을 뚫고 무려 **{result}P**를 획득했습니다! 골드 등급이 코앞입니다!"
    elif result >= 75:
        color = discord.Color.gold()
        title = "🎉 축하합니다! 대박 당첨!"
        desc = f"**+{result}P**를 얻으셨습니다!"
    else:
        color = discord.Color.green()
        title = "✨ 소소한 이득!"
        desc = f"**+{result}P**를 획득했습니다!"

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.add_field(name="현재 잔여 포인트", value=f"`{final_points} P`", inline=False)
    
    await ctx.send(embed=embed)


@bot.command(name="가위바위보")
async def rock_paper_scissors(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!가위바위보 [가위/바위/보] [배팅금액]`")
    
    if bet < 10:
        return await ctx.send("❌ 최소 배팅 금액은 `10 P` 이상이어야 합니다.")
        
    current_points = await get_user_points(ctx.author.id)
    if current_points < bet:
        return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points}P`)")

    bot_choice = random.choice(choices)
    
    if choice == bot_choice:
        result = "draw"
    elif (choice == "가위" and bot_choice == "보") or \
         (choice == "바위" and bot_choice == "가위") or \
         (choice == "보" and bot_choice == "바위"):
        result = "win"
    else:
        result = "lose"

    if result == "win":
        await add_user_points(ctx.guild, ctx.author, bet)
        final_points = await get_user_points(ctx.author.id)
        embed = discord.Embed(
            title="✌️🖐️✊ 가위바위보 승리!",
            description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n🎉 승리하여 **+{bet}P**를 획득했습니다!",
            color=discord.Color.green()
        )
    elif result == "draw":
        final_points = current_points
        embed = discord.Embed(
            title="✌️🖐️✊ 가위바위보 무승부!",
            description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n비겼으므로 배팅한 포인트를 그대로 돌려받습니다.",
            color=discord.Color.light_grey()
        )
    else:
        # [수정된 밸런스] 위로 포인트 환급을 10%에서 5%로 축소
        consolation = max(1, int(bet * 0.05))
        await add_user_points(ctx.guild, ctx.author, -bet + consolation)
        final_points = await get_user_points(ctx.author.id)
        embed = discord.Embed(
            title="✌️🖐️✊ 가위바위보 패배...",
            description=f"유저: **{choice}** vs 봇: **{bot_choice}**\n\n😭 패배하여 `{bet}P`를 잃었지만, 위로 포인트 **+{consolation}P**를 받았어요!",
            color=discord.Color.red()
        )

    embed.add_field(name="현재 보유 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="묵찌빠")
async def muk_jji_bba(ctx, choice: str, bet: int):
    if not await check_command_channel(ctx):
        return

    choices = ["가위", "바위", "보"]
    if choice not in choices:
        return await ctx.send("❌ 올바른 선택을 해주세요: `!묵찌빠 [가위/바위/보] [배팅금액]`")
    
    if bet < 20:
        return await ctx.send("❌ 묵찌빠 최소 배팅 금액은 `20 P` 이상이어야 합니다.")
        
    current_points = await get_user_points(ctx.author.id)
    if current_points < bet:
        return await ctx.send(f"❌ 보유 포인트가 부족합니다. (현재 `{current_points}P`)")

    bot_choice1 = random.choice(choices)
    
    if choice == bot_choice1:
        bot_choice1 = random.choice([c for c in choices if c != choice])

    user_attacker = (
        (choice == "가위" and bot_choice1 == "보") or
        (choice == "바위" and bot_choice1 == "가위") or
        (choice == "보" and bot_choice1 == "바위")
    )

    bot_choice2 = random.choice(choices)
    user_choice2 = choice

    embed = discord.Embed(title="👊✌️🖐️ 스릴만점 묵찌빠 대결!", color=discord.Color.blurple())
    embed.add_field(
        name="1라운드 (주도권 잡기)",
        value=f"유저: **{choice}** vs 봇: **{bot_choice1}** ➔ **{'유저' if user_attacker else '봇'}** 공격 선제 잡기!",
        inline=False
    )

    if user_choice2 == bot_choice2:
        if user_attacker:
            win_amount = int(bet * 1.5)
            await add_user_points(ctx.guild, ctx.author, win_amount)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(
                name="2라운드 (묵찌빠 완성!)",
                value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}** (일치!)\n\n🔥 **공격 성공! 묵~찌~빠!** **+{win_amount}P** 획득!",
                inline=False
            )
            embed.color = discord.Color.gold()
        else:
            # [수정된 밸런스] 패배 시 위로 포인트를 15%에서 5%로 축소
            consolation = max(1, int(bet * 0.05))
            await add_user_points(ctx.guild, ctx.author, -bet + consolation)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(
                name="2라운드 (묵찌빠 완료)",
                value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}** (일치!)\n\n💀 봇의 공격에 당했습니다... 위로 포인트 **+{consolation}P** 지급!",
                inline=False
            )
            embed.color = discord.Color.dark_red()
    else:
        bot_wins_final = random.choice([True, False])
        if not bot_wins_final:
            win_amount = int(bet * 1.2)
            await add_user_points(ctx.guild, ctx.author, win_amount)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(
                name="2라운드 (치열한 난투)",
                value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}**\n\n✨ 치열한 묵찌빠 랠리 끝에 유저 승리! **+{win_amount}P** 획득!",
                inline=False
            )
            embed.color = discord.Color.green()
        else:
            # [수정된 밸런스] 패배 시 위로 포인트를 15%에서 5%로 축소
            consolation = max(1, int(bet * 0.05))
            await add_user_points(ctx.guild, ctx.author, -bet + consolation)
            final_points = await get_user_points(ctx.author.id)
            embed.add_field(
                name="2라운드 (치열한 난투)",
                value=f"유저: **{user_choice2}** vs 봇: **{bot_choice2}**\n\n😭 아쉬운 차이로 패배했습니다... 위로 포인트 **+{consolation}P** 환급!",
                inline=False
            )
            embed.color = discord.Color.red()

    embed.add_field(name="현재 보유 포인트", value=f"`{final_points} P`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="포인트지급")
@commands.has_permissions(administrator=True)
async def give_points(ctx, member: discord.Member, amount: int):
    new_points = await add_user_points(ctx.guild, member, amount)
    await ctx.send(f"✅ {member.mention} 님에게 `{amount} P`를 지급했습니다. (현재: `{new_points} P`)")


@bot.command(name="포인트차감")
@commands.has_permissions(administrator=True)
async def remove_points(ctx, member: discord.Member, amount: int):
    new_points = await add_user_points(ctx.guild, member, -amount)
    await ctx.send(f"✅ {member.mention} 님의 포인트를 `{amount} P` 차감했습니다. (현재: `{new_points} P`)")


@bot.command(name="포인트리셋")
@commands.has_permissions(administrator=True)
async def reset_points(ctx, member: discord.Member):
    current_points = await get_user_points(member.id)
    if current_points > 0:
        new_points = await add_user_points(ctx.guild, member, -current_points)
    else:
        new_points = current_points
    await ctx.send(f"🔄 {member.mention} 님의 포인트를 `0 P`로 초기화했습니다.")


@bot.command(name="주간베스트", aliases=["명예의전당"])
@commands.has_permissions(administrator=True)
async def weekly_best(ctx):
    await ctx.send("🔍 최근 작품들을 분석하며 반응을 집계 중입니다...")
    
    share_channel = ctx.guild.get_channel(WORK_SHARE_CHANNEL_ID)
    if not share_channel:
        return await ctx.send("❌ 작품 공유 채널을 찾을 수 없습니다.")

    best_message = None
    max_reactions = 0

    async for message in share_channel.history(limit=100):
        if message.author.bot:
            continue
            
        total_reactions = sum(reaction.count for reaction in message.reactions)
        
        if total_reactions > max_reactions:
            max_reactions = total_reactions
            best_message = message

    if best_message is None or max_reactions == 0:
        return await ctx.send("❌ 최근 올라온 작품 중 반응이 있는 게시글이 없습니다.")

    embed = discord.Embed(
        title="🏆 이주의 베스트 작품 선정!",
        description=f"{best_message.author.mention} 님의 작품이 이번 주 명예의 전당에 올랐습니다!\n\n"
                    f"**💬 받은 반응 수:** `{max_reactions}개`\n"
                    f"[👉 원본 게시글 보러가기]({best_message.jump_url})",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    if best_message.attachments:
        embed.set_image(url=best_message.attachments[0].url)
    elif best_message.content:
        embed.add_field(name="작품 내용 요약", value=best_message.content[:100] + "...", inline=False)

    embed.set_footer(text="매주 최고의 퀄리티를 보여준 분께 영광을!")
    
    await ctx.send(embed=embed)


@bot.command(name="업데이트확인", aliases=["봇상태"])
@commands.has_permissions(administrator=True)
async def update_check(ctx):
    embed = discord.Embed(
        title="봇 실행 정보",
        color=discord.Color.green(),
        timestamp=bot_started_at,
    )
    embed.add_field(name="버전", value=f"`{get_bot_version()}`", inline=True)
    embed.add_field(
        name="시작 시간",
        value=discord.utils.format_dt(bot_started_at, style="F"),
        inline=False,
    )
    await ctx.send(embed=embed)


# ==================== [보안 및 유틸리티 함수] ====================

def sanitize_text(text):
    if not text:
        return "[내용 없음]"

    text = re.sub(r'https?://\S+', '[LINK]', text)
    text = re.sub(r'discord\.gg/\S+', '[INVITE]', text)
    text = re.sub(r'\S+@\S+', '[EMAIL]', text)
    text = re.sub(r'\d{2,3}-\d{3,4}-\d{4}', '[PHONE]', text)
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


def is_ticket_or_archive_channel(channel):
    return (
        isinstance(channel, discord.TextChannel)
        and (
            channel.name.startswith("티켓-")
            or channel.name.startswith("보관-티켓-")
        )
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


async def update_commission_progress(channel, progress):
    now = datetime.now().isoformat()
    status = "completed" if progress == 100 else "in_progress"

    async with aiosqlite.connect(DATABASE) as db:
        if progress == 100:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?,
                    status = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now, now, channel.id)
            )
        else:
            await db.execute(
                """
                UPDATE commissions
                SET progress = ?,
                    status = ?,
                    updated_at = ?
                WHERE ticket_channel = ?
                """,
                (progress, status, now, channel.id)
            )

        await db.commit()


def get_commission_category_from_embed(embed):
    title = embed.title or ""

    if "GFX" in title:
        return "GFX"

    if "로고" in title:
        return "로고"

    if "복장" in title or "Roblox" in title:
        return "Roblox 복장"

    return "커미션"


def get_progress_from_embed(embed):
    text = embed.description or ""
    match = re.search(r"진행률\s*:\s*(\d+)%", text)
    return int(match.group(1)) if match else 0


async def get_last_message_time(channel):
    try:
        async for msg in channel.history(limit=1):
            return msg.created_at.replace(tzinfo=None).isoformat()
    except Exception:
        pass

    return channel.created_at.replace(tzinfo=None).isoformat()


async def read_commission_from_ticket(channel):
    owner = await find_ticket_owner(channel)
    designer_id = await find_ticket_designer_id(channel)
    category = "커미션"
    progress = 0

    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            if embed.title and "신청서" in embed.title:
                category = get_commission_category_from_embed(embed)

            if embed.title == "📌 커미션 진행":
                progress = get_progress_from_embed(embed)

    is_archived = channel.name.startswith("보관-티켓-")
    is_completed = is_archived or progress == 100
    status = "completed" if is_completed else "in_progress"
    completed_at = await get_last_message_time(channel) if is_completed else None

    return {
        "ticket_channel": channel.id,
        "customer_id": owner.id if owner else None,
        "designer_id": designer_id,
        "category": category,
        "status": status,
        "progress": 100 if is_completed else progress,
        "created_at": channel.created_at.replace(tzinfo=None).isoformat(),
        "completed_at": completed_at,
        "updated_at": datetime.now().isoformat(),
    }


async def upsert_commission_record(data):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """
            INSERT INTO commissions(
                ticket_channel,
                customer_id,
                designer_id,
                category,
                status,
                progress,
                created_at,
                completed_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket_channel) DO UPDATE SET
                customer_id = excluded.customer_id,
                designer_id = excluded.designer_id,
                category = excluded.category,
                status = excluded.status,
                progress = excluded.progress,
                completed_at = COALESCE(commissions.completed_at, excluded.completed_at),
                updated_at = excluded.updated_at
            """,
            (
                data["ticket_channel"],
                data["customer_id"],
                data["designer_id"],
                data["category"],
                data["status"],
                data["progress"],
                data["created_at"],
                data["completed_at"],
                data["updated_at"],
            )
        )
        await db.commit()


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


# ==================== [티켓 패널 및 업무 명령어] ====================

@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):
    file = discord.File("price.png", filename="price.png")
    file2 = discord.File("price2.png", filename="price2.png")

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

    embed2 = discord.Embed(
        color=0x5865F2
    )
    embed2.set_image(url="attachment://price2.png")

    await ctx.send(
        files=[file, file2],
        embeds=[embed, embed2],
        view=TicketOpenView()
    )


@bot.command(name="통계")
@commands.has_permissions(administrator=True)
async def stats(ctx):
    embed = await build_monthly_stats_embed(ctx.guild)
    message = await ctx.send(embed=embed)
    await save_monthly_stats_message(message)
    await ctx.reply(
        "✅ 월간 통계 패널을 등록했습니다. 앞으로 이 메시지를 자동 수정합니다.",
        mention_author=False,
        delete_after=5
    )


@bot.command(name="통계동기화", aliases=["이전티켓적용", "티켓통계동기화"])
@commands.has_permissions(administrator=True)
async def sync_existing_tickets(ctx):
    notice = await ctx.send("🔄 기존 티켓을 통계 DB에 동기화하는 중입니다.")
    synced = 0
    skipped = 0

    for channel in ctx.guild.text_channels:
        if not is_ticket_or_archive_channel(channel):
            continue

        try:
            data = await read_commission_from_ticket(channel)
            await upsert_commission_record(data)
            synced += 1
        except Exception as e:
            skipped += 1
            print(f"[통계 동기화 실패] channel={channel.id} error={e}")

    try:
        await update_monthly_stats_message(bot)
    except Exception as e:
        print(f"[월간 통계 즉시 갱신 실패] {e}")

    await notice.edit(
        content=(
            "✅ 기존 티켓 통계 동기화 완료\n"
            f"적용: {synced}개\n"
            f"실패: {skipped}개"
        )
    )


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
    embed.add_field(name="대상 디자이너", value=member.mention, inline=False)
    embed.add_field(name="은행", value=bank_name, inline=False)
    embed.add_field(name="계좌번호", value=f"`{mask_account(account_number)}`", inline=False)
    embed.add_field(name="예금주", value=holder, inline=False)

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
        log_embed.set_footer(text="개인정보는 저장되지 않았증니다.")

        await log_channel.send(
            content=(
                f"🔒 {ticket_owner.mention} 님의 티켓이 종료되었습니다."
                if ticket_owner
                else "🔒 티켓이 종료되었습니다."
            ),
            embed=log_embed
        )

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


@bot.command(name="티켓삭제", aliases=["티켓제거", "삭제"])
async def delete_ticket_by_command(ctx):
    if not is_ticket_or_archive_channel(ctx.channel):
        return await ctx.send("❌ 티켓 채널에서만 사용할 수 있습니다.")

    channel = ctx.channel
    guild = ctx.guild
    designer_id = await find_ticket_designer_id(channel)
    deleter = guild.get_member(ctx.author.id)

    if not can_manage_ticket(deleter, ctx.author.id, designer_id):
        return await ctx.send("❌ 담당 디자이너 또는 관리자만 티켓을 삭제할 수 있습니다.")

    await ctx.send("🗑️ 티켓을 삭제합니다.")
    await asyncio.sleep(3)
    await delete_ticket_channel(channel, ctx.author)


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
        if msg.author != bot.user or not msg.embeds:
            continue

        embed = msg.embeds[0]
        if embed.title != "📌 커미션 진행" or not embed.description:
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
        await update_commission_progress(ctx.channel, percent)
        await ctx.send("✅ 진행률이 변경되었습니다.", delete_after=3)
        return

    await ctx.send("진행 패널을 찾지 못했습니다.")


@bot.command(name="예상")
@commands.has_permissions(administrator=True)
async def estimate(ctx, days: str):
    if days not in ["1일", "2일", "3일"]:
        return await ctx.send("사용법: `!예상 1일|2일|3일`")

    async for msg in ctx.channel.history(limit=30):
        if msg.author != bot.user or not msg.embeds:
            continue

        embed = msg.embeds[0]
        if embed.title != "📌 커미션 진행" or not embed.description:
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
        if msg.author != bot.user or not msg.embeds:
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
            await update_commission_progress(ctx.channel, 100)
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


@bot.command(name="상태")
async def change_ticket_status(ctx, *, status: str):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("티켓 채널에서만 사용할 수 있습니다.")
    designer_id = await find_ticket_designer_id(ctx.channel)
    member = ctx.guild.get_member(ctx.author.id)
    if not can_manage_ticket(member, ctx.author.id, designer_id):
        return await ctx.send("담당 디자이너 또는 관리자만 상태를 변경할 수 있습니다.")
    status = status.strip()
    if not status or len(status) > 50:
        return await ctx.send("상태는 1~50자로 입력해주세요.")
    async for message in ctx.channel.history(limit=50):
        if message.author != bot.user or not message.embeds:
            continue
        embed = message.embeds[0]
        if embed.title != "📌 커미션 진행" or not embed.description:
            continue
        lines = embed.description.splitlines()
        if len(lines) < 4:
            continue
        lines[2] = f"📌 상태 : {status}"
        embed.description = "\n".join(lines)
        await message.edit(embed=embed)
        await ctx.send(f"📌 {ctx.author.mention}님이 상태를 변경했습니다.\n상태: {status}")
        return
    await ctx.send("진행률 메시지를 찾지 못했습니다.")


@bot.command(name="DM정리", aliases=["dm정리"])
async def clear_ticket_dm(ctx):
    if not is_ticket_channel(ctx.channel):
        return await ctx.send("티켓 채널에서만 사용할 수 있습니다.")
    designer_id = await find_ticket_designer_id(ctx.channel)
    member = ctx.guild.get_member(ctx.author.id)
    if not can_manage_ticket(member, ctx.author.id, designer_id):
        return await ctx.send("담당 디자이너 또는 관리자만 DM을 정리할 수 있습니다.")
    designer = await fetch_member_or_none(ctx.guild, designer_id)
    deleted_count = await delete_ticket_dm_messages(bot.user, designer, ctx.channel)
    await ctx.send(f"🧹 이 티켓의 관리 DM {deleted_count}개를 삭제했습니다.")


@bot.command(name="DM전체정리", aliases=["dm전체정리"])
@commands.has_permissions(administrator=True)
async def clear_all_designer_dm(ctx, member: discord.Member):
    deleted_count = await delete_all_bot_dm_messages(bot.user, member)
    await ctx.send(f"🧹 {member.mention}님에게 보낸 봇 DM {deleted_count}개를 삭제했습니다.")


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


@tasks.loop(minutes=30)
async def monthly_stats_updater():
    try:
        await update_monthly_stats_message(bot)
    except Exception as e:
        print(f"[월간 통계 갱신 실패] {e}")


@monthly_stats_updater.before_loop
async def before_monthly_stats_updater():
    await bot.wait_until_ready()


@tasks.loop(hours=24)
async def database_backup_task():
    try:
        backup_path = await backup_database()
        if backup_path:
            print(f"[DB backup] {backup_path}")
    except Exception as error:
        print(f"[DB backup failed] {error}")


@database_backup_task.before_loop
async def before_database_backup_task():
    await bot.wait_until_ready()


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
                "`!티켓생성` `!인증패널`\n"
                "`!진행 0|25|50|75|100` `!예상 1일|2일|3일` `!완료`\n"
                "`!포인트` `!뽑기` `!가위바위보` `!묵찌빠`"
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
async def setup_hook():
    if not os.getenv("OPENAI_API_KEY"):
        print("Auto translator disabled: OPENAI_API_KEY is missing")
        return

    await bot.load_extension("database.services.auto_translator")
    print("✅ 자동 번역 기능 로드 완료")


@bot.event
async def on_ready():
    global daily_notice, persistent_views_registered, update_notice_sent

    await create_tables()

    print("on_ready")
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")

    if not persistent_views_registered:
        bot.add_view(TicketOpenView())
        bot.add_view(CategoryView())
        bot.add_view(StarRatingView())
        bot.add_view(ProgressView())
        bot.add_view(PaymentView())
        bot.add_view(TicketCloseView())
        bot.add_view(VerifyView())
        persistent_views_registered = True

    if daily_notice is None:
        print("DailyNotice 생성 전")
        daily_notice = DailyNotice(bot)
        print("DailyNotice 생성 완료")

    if not monthly_stats_updater.is_running():
        monthly_stats_updater.start()

    try:
        await update_monthly_stats_message(bot)
    except Exception as error:
        print(f"[Monthly stats startup refresh failed] {error}")

    await database_backup_task()
    if not database_backup_task.is_running():
        database_backup_task.start()

    if not update_notice_sent:
        log_channel = discord.utils.get(bot.get_all_channels(), name=LOG_CHANNEL_NAME)
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(
                f"✅ 봇 업데이트 완료\n버전: `{get_bot_version()}`\n"
                f"시작: {discord.utils.format_dt(bot_started_at, style='F')}"
            )
        update_notice_sent = True

    print("✨ 영속성 버튼 등록 완료!")


if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
