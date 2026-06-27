import discord
import os
import asyncio
from io import BytesIO
from datetime import datetime
import re
from discord.ext import commands, tasks
from database.database import create_tables
from zoneinfo import ZoneInfo
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



# ==================== [티켓 닫기 View] ====================

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 티켓 닫기",
        style=discord.ButtonStyle.danger,
        custom_id="close_ticket"
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        try:

            # 봇 차단
            if interaction.user.bot:
                return

            # 개발자만 티켓 종료 가능
            if interaction.user.id not in DEVELOPER_IDS:
                return await interaction.response.send_message(
                    "❌ 관리자만 티켓을 종료할 수 있습니다.",
                    ephemeral=True
                )

            await interaction.response.defer()

            channel = interaction.channel
            channel_name = channel.name
            guild = interaction.guild

            if "티켓" in channel_name:

                ticket_owner = None

                try:
                    owner_id = int(channel_name.split("-")[-1])

                    ticket_owner = (
                        guild.get_member(owner_id)
                        or await guild.fetch_member(owner_id)
                    )

                except Exception:
                    ticket_owner = interaction.user

                await interaction.followup.send(
                    "💾 안전하게 구매로그를 정리하는 중입니다..."
                )

                # ==============================
                # 🔒 공개용 안전 구매로그 생성
                # ==============================

                message_count = 0
                attachment_count = 0
                participants = set()

                recent_messages = []

                async for msg in channel.history(
                    limit=100,
                    oldest_first=False
                ):

                    if msg.author.bot:
                        continue

                    message_count += 1
                    participants.add(msg.author.display_name)

                    if msg.attachments:
                        attachment_count += len(msg.attachments)

                    if len(recent_messages) < 3:

                        clean_content = sanitize_text(msg.content)

                        if not clean_content.strip():
                            clean_content = "[파일 또는 이미지]"

                        recent_messages.append(
                            f"• {msg.author.display_name}: {clean_content}"
                        )

                created_at = channel.created_at
                closed_at = datetime.now(created_at.tzinfo)

                duration = closed_at - created_at

                hours = duration.seconds // 3600
                minutes = (duration.seconds % 3600) // 60

                # ==============================
                # 구매로그 채널
                # ==============================

                log_channel = discord.utils.get(
                    guild.text_channels,
                    name=LOG_CHANNEL_NAME
                )

                if log_channel:

                    safe_log_embed = discord.Embed(
                        title="🧾 구매/상담 로그",
                        color=0x5865F2,
                        timestamp=datetime.now()
                    )

                    safe_log_embed.add_field(
                        name="👤 고객",
                        value=f"{ticket_owner.mention}",
                        inline=True
                    )

                    safe_log_embed.add_field(
                        name="🔒 종료자",
                        value=f"{interaction.user.mention}",
                        inline=True
                    )

                    safe_log_embed.add_field(
                        name="💬 메시지 수",
                        value=str(message_count),
                        inline=True
                    )

                    safe_log_embed.add_field(
                        name="⏱ 상담 시간",
                        value=f"{hours}시간 {minutes}분",
                        inline=True
                    )

                    safe_log_embed.add_field(
                        name="📎 첨부파일",
                        value=f"{attachment_count}개",
                        inline=True
                    )

                    safe_log_embed.add_field(
                        name="👥 참여자",
                        value=", ".join(participants),
                        inline=False
                    )


                    safe_log_embed.set_footer(
                        text="전체 대화내용 및 개인정보는 저장되지 않았습니다."
                    )

                    await log_channel.send(
                        content=f"🔒 {ticket_owner.mention} 님의 티켓이 종료되었습니다.",
                        embed=safe_log_embed
                    )

                # ==============================
                # 구매자 역할 자동 지급
                # ==============================

                try:

                    buyer_role = guild.get_role(BUYER_ROLE_ID)

                    if buyer_role and ticket_owner:

                        if buyer_role not in ticket_owner.roles:

                            await ticket_owner.add_roles(
                                buyer_role,
                                reason="커미션 완료 자동 구매자 역할 지급"
                            )

                            try:

                                success_role_embed = discord.Embed(
                                    title="🎉 구매자 역할 지급 완료",
                                    description=(
                                        f"`{guild.name}` 서버에서\n"
                                        f"구매자 역할이 지급되었습니다!"
                                    ),
                                    color=discord.Color.green()
                                )

                                await ticket_owner.send(
                                    embed=success_role_embed
                                )

                            except:
                                pass

                except Exception as role_err:
                    print(f"[구매자 역할 지급 실패] {role_err}")


# ==================== [티켓 오픈 View] ====================
        
last_announce_date = None            

@tasks.loop(minutes=1)
async def auto_announce():
    global last_announce_date

    now = datetime.now(ZoneInfo("Asia/Seoul"))

    if now.hour == 18 and now.minute == 0:

        today = now.date()

        if last_announce_date == today:
            return

        last_announce_date = today

        channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)

        if channel:
            try:
                await channel.send(
                    f"""<@&{CUSTOMER_ROLE_ID}>

🎨 **Roblox GFX 커미션 받습니다!**

📸 예시작은 <#1505178799950532720> 에서 확인해주세요.
💳 구매는 <#1505102694917079132> 를 이용해주세요.

감사합니다 🙏
"""
                )

            except Exception as e:
                print(f"[자동공지 실패] {e}")
    

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

    if not auto_announce.is_running():
        auto_announce.start()

    print("✨ 영속성 버튼 등록 완료!")
    print("📢 자동 판매공지 시작")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
