import discord
import os
import asyncio
from io import BytesIO
from datetime import datetime
import re
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

TOKEN = os.getenv("TOKEN")

REVIEW_CHANNEL_NAME = "후기"
LOG_CHANNEL_NAME = "구매로그"
ANNOUNCE_CHANNEL_NAME = "︱🔊ㅣ판매공지"


# 자동 지급할 구매자 역할 ID
BUYER_ROLE_ID = 1505076370332586155
CUSTOMER_ROLE_ID = 1505074732700008531

# 알림을 받을 개발자(관리자)들의 디스코드 고유 ID 리스트
DEVELOPER_IDS = [
    1292859064065458189,
    1468584582113919129,
    1465051418162626763,
    859756809865789451
]

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


# ==================== [자동 별점 및 후기 View 시스템] ====================

class StarRatingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⭐ 1점", style=discord.ButtonStyle.secondary, custom_id="star_1")
    async def star_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 1)

    @discord.ui.button(label="⭐ 2점", style=discord.ButtonStyle.secondary, custom_id="star_2")
    async def star_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 2)

    @discord.ui.button(label="⭐ 3점", style=discord.ButtonStyle.secondary, custom_id="star_3")
    async def star_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 3)

    @discord.ui.button(label="⭐ 4점", style=discord.ButtonStyle.secondary, custom_id="star_4")
    async def star_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 4)

    @discord.ui.button(label="⭐ 5점", style=discord.ButtonStyle.success, custom_id="star_5")
    async def star_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 5)

    async def process_rating(self, interaction: discord.Interaction, stars: int):
        try:
            await interaction.response.defer(ephemeral=True)
            ticket_owner = interaction.user

            guild = None
            if interaction.message.embeds:
                for g in bot.guilds:
                    if discord.utils.get(g.text_channels, name=REVIEW_CHANNEL_NAME):
                        guild = g
                        break

            if not guild:
                guild = bot.guilds[0] if bot.guilds else None

            if not guild:
                return await interaction.followup.send(
                    "연동된 서버를 찾을 수 없습니다.",
                    ephemeral=True
                )

            review_channel = discord.utils.get(
                guild.text_channels,
                name=REVIEW_CHANNEL_NAME
            )

            if not review_channel:
                review_channel = next(
                    (ch for ch in guild.text_channels if "후기" in ch.name),
                    None
                )

            if review_channel:
                star_emojis = "⭐" * stars

                review_embed = discord.Embed(
                    title="✨ 소중한 커미션 후기가 도착했습니다!",
                    color=0xFEE75C,
                    timestamp=datetime.now()
                )

                review_embed.set_thumbnail(url=ticket_owner.display_avatar.url)

                review_embed.add_field(
                    name="👤 작성자(오너)",
                    value=f"{ticket_owner.mention} ({ticket_owner.name})",
                    inline=True
                )

                review_embed.add_field(
                    name="📊 만족도 별점",
                    value=f"**{star_emojis} ({stars} / 5점)**",
                    inline=True
                )

                review_embed.set_footer(
                    text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏"
                )

                await review_channel.send(embed=review_embed)

                success_view = discord.ui.View()

                try:
                    invite = await review_channel.create_invite(
                        max_age=300,
                        max_uses=1
                    )

                    success_view.add_item(
                        discord.ui.Button(
                            label="😄 내가 쓴 후기 보러가기",
                            url=invite.url,
                            style=discord.ButtonStyle.link
                        )
                    )
                except:
                    pass

                await interaction.followup.send(
                    f"🎉 성공적으로 **{stars}점** 별점이 제출되었습니다!",
                    view=success_view,
                    ephemeral=True
                )

                disabled_view = discord.ui.View()

                for i in range(1, 6):
                    style = (
                        discord.ButtonStyle.success
                        if i == 5
                        else discord.ButtonStyle.secondary
                    )

                    disabled_view.add_item(
                        discord.ui.Button(
                            label=f"⭐ {i}점",
                            style=style,
                            custom_id=f"star_{i}",
                            disabled=True
                        )
                    )

                await interaction.message.edit(view=disabled_view)

            else:
                await interaction.followup.send(
                    "후기 채널을 찾을 수 없습니다.",
                    ephemeral=True
                )

        except Exception as e:
            print(f"[별점 등록 에러] {e}")


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

                # ==============================
                # 유저 DM
                # ==============================

                try:

                    dm_embed = discord.Embed(
                        title="💌 서비스를 이용해 주셔서 감사합니다!",
                        description=(
                            "진행하시던 커미션 완료되어 티켓이 종료되었습니다.\n"
                            "아래 버튼을 통해 만족도 별점을 남겨주세요!"
                        ),
                        color=0x5865F2
                    )

                    await ticket_owner.send(
                        embed=dm_embed,
                        view=StarRatingView()
                    )

                except Exception as dm_e:
                    print(f"[DM 실패] {dm_e}")

                await interaction.followup.send(
                    "⚠️ 로그 정리 완료! 채널은 5초 후 삭제됩니다."
                )

                await asyncio.sleep(5)
                await channel.delete()

            else:

                await interaction.followup.send(
                    "❌ 올바른 티켓 채널이 아닙니다.",
                    ephemeral=True
                )

        except Exception as e:
            print(f"[티켓 닫기 에러] {e}")


# ==================== [티켓 오픈 View] ====================

class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 티켓 열기",
        style=discord.ButtonStyle.primary,
        custom_id="open_ticket_btn"
    )
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        guild = interaction.guild
        user = interaction.user

        ticket_channel_name = f"티켓-{user.id}"

        existing_channel = discord.utils.get(
            guild.text_channels,
            name=ticket_channel_name
        )

        if existing_channel:
            return await interaction.response.send_message(
                f"❌ 이미 생성된 티켓이 존재합니다: {existing_channel.mention}",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False
            ),

            user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            ),

            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            )
        }

        for dev_id in DEVELOPER_IDS:
            dev_member = guild.get_member(dev_id)

            if dev_member:
                overwrites[dev_member] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True
                )

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            overwrites=overwrites
        )

        welcome_embed = discord.Embed(
            title="🎫 문의 티켓이 생성되었습니다",
            description=(
                f"안녕하세요 {user.mention}님!\n"
                "문의 내용을 작성해 주세요.\n"
                "커미션 종료 시 아래 🔒 버튼을 눌러주세요."
            ),
            color=0x5865F2,
            timestamp=datetime.now()
        )

        await ticket_channel.send(
            embed=welcome_embed,
            view=TicketCloseView()
        )

        await interaction.followup.send(
            f"✅ 티켓 생성 완료: {ticket_channel.mention}",
            ephemeral=True
        )

        # ==============================
        # 구매로그 생성 알림
        # ==============================

        log_channel = discord.utils.get(
            guild.text_channels,
            name=LOG_CHANNEL_NAME
        )

        if log_channel:

            open_log_embed = discord.Embed(
                title="📩 새로운 티켓 생성",
                description=(
                    f"**채널:** {ticket_channel.mention}\n"
                    f"**생성자:** {user.mention}"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now()
            )

            await log_channel.send(embed=open_log_embed)

        # ==============================
        # 관리자 DM 알림
        # ==============================

        dev_dm_embed = discord.Embed(
            title="🔔 신규 티켓 생성",
            description=(
                f"**서버명:** `{guild.name}`\n"
                f"**생성자:** {user.mention}\n"
                f"**바로가기:** {ticket_channel.mention}"
            ),
            color=0x5865F2,
            timestamp=datetime.now()
        )

        for dev_id in DEVELOPER_IDS:
            try:
                developer = (
                    guild.get_member(dev_id)
                    or await bot.fetch_user(dev_id)
                )

                if developer and not developer.bot:
                    await developer.send(embed=dev_dm_embed)

            except Exception as dm_err:
                print(f"[개발자 DM 실패 - {dev_id}] {dm_err}")

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

        for guild in bot.guilds:

            channel = discord.utils.get(
                guild.text_channels,
                name=ANNOUNCE_CHANNEL_NAME
            )

            if channel:
                try:
                    await channel.send(
                        f"""<@&{CUSTOMER_ROLE_ID}>

🎨 **Roblox GFX 커미션 받습니다!**

📸 예시작은 예시작 채널에서 확인해주세요.
💳 구매는 구매 채널을 이용해주세요.
🎫 문의는 티켓을 열어주세요.

감사합니다 🙏
"""
                    )

                except Exception as e:
                    print(f"[자동공지 실패] {e}")
    

# ==================== [티켓 패널 명령어] ====================

@bot.command(name="티켓생성")
@commands.has_permissions(administrator=True)
async def t_create_panel(ctx):

    embed = discord.Embed(
        title="💼 커미션 및 문의 상담 공간",
        description=(
            "상담, 구매 진행, 문의사항이 있으시다면\n"
            "아래 📩 버튼을 눌러주세요!"
        ),
        color=0x5865F2
    )

    await ctx.send(
        embed=embed,
        view=TicketOpenView()
    )


# ==================== [봇 시작 시스템] ====================

@bot.event
async def on_ready():
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")
    print("--------------------------------------------------")


@bot.event
async def setup_hook():
    bot.add_view(TicketOpenView())
    bot.add_view(StarRatingView())
    bot.add_view(TicketCloseView())

    auto_announce.start()

    print("✨ 영속성 버튼 등록 완료!")
    print("📢 자동 판매공지 시작")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN 환경변수를 찾을 수 없습니다.")
