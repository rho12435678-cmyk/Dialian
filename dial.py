import discord
from discord.ext import commands
import os
import asyncio
from io import BytesIO
from datetime import datetime

TOKEN = os.getenv("TOKEN")

# 채널 이름 설정 (내 서버에 있는 채널 이름과 정확히 일치해야 합니다)
REVIEW_CHANNEL_NAME = "😄후기"
LOG_CHANNEL_NAME = "구매로그"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== [새로워진 자동 별점 및 후기 View 시스템] ====================

# 1. 유저가 DM에서 누를 1점~5점 별점 버튼 정의
class StarRatingView(discord.ui.View):
    def __init__(self, guild, ticket_owner):
        super().__init__(timeout=None) # 24시간 버튼이 죽지 않게 대기
        self.guild = guild
        self.ticket_owner = ticket_owner

    # 별점 버튼을 처리하는 공통 함수
    async def process_rating(self, interaction: discord.Interaction, stars: int):
        try:
            # 서버의 후기 채널 찾기
            review_channel = discord.utils.get(self.guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if not review_channel:
                review_channel = next((ch for ch in self.guild.text_channels if "후기" in ch.name), None)

            if review_channel:
                # 별점 개수만큼 이모지 생성
                star_emojis = "⭐" * stars
                
                # 후기 채널에 올라갈 디자인 임베드 카드
                review_embed = discord.Embed(
                    title="✨ 소중한 커미션 후기가 도착했습니다!",
                    color=0xFEE75C, # 디스코드 공식 골드 옐로우 색상
                    timestamp=datetime.now()
                )
                review_embed.set_thumbnail(url=self.ticket_owner.display_avatar.url)
                review_embed.add_field(name="👤 작성자(오너)", value=f"{self.ticket_owner.mention} ({self.ticket_owner.name})", inline=True)
                review_embed.add_field(name="📊 만족도 별점", value=f"**{star_emojis} ({stars} / 5점)**", inline=True)
                review_embed.set_footer(text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏")

                # 후기 채널에 알림 전송
                await review_channel.send(embed=review_embed)

                # DM을 보낸 유저에게 고마움 표시 및 초고속 이동 링크 버튼 제공
                success_view = discord.ui.View()
                # 해당 서버의 후기 채널로 바로 순간이동하는 초대장 링크형 버튼 추가
                try:
                    invite = await review_channel.create_invite(max_age=300, max_uses=1)
                    success_view.add_item(discord.ui.Button(label="😄 내가 쓴 후기 보러가기", url=invite.url, style=discord.ButtonStyle.link))
                except:
                    pass # 인바이트 생성 권한이 없을 경우 버튼 생략

                await interaction.response.send_message(
                    f"🎉 성공적으로 **{stars}점** 별점이 제출되었습니다! 소중한 의견 감사합니다.", 
                    view=success_view, 
                    ephemeral=True
                )
                
                # 별점을 한 번 누르면 다른 버튼은 더 이상 못 누르게 리스트 정지
                self.stop()
            else:
                await interaction.response.send_message("서버의 후기 채널을 찾을 수 없습니다. 관리자에게 문의해 주세요.", ephemeral=True)
        except Exception as e:
            print(f"[별점 등록 에러] {e}")

    # 1점부터 5점까지 버튼 배치
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

    @discord.ui.button(label="⭐ 5점", style=discord.ButtonStyle.success, custom_id="star_5") # 5점은 강조색상(초록)
    async def star_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 5)


# 2. 티켓 닫기 버튼 정의 (닫힐 때 대화록 백업 + 디자인된 별점 DM 자동 발송)
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channel = interaction.channel
            channel_name = channel.name
            guild = interaction.guild
            
            if "티켓-" in channel_name:
                await interaction.response.defer()
                
                # 채널 이름에서 방 주인 역추적
                target_username = channel_name.replace("티켓-", "").lower()
                ticket_owner = None
                
                for member in guild.members:
                    if member.name.lower() == target_username:
                        ticket_owner = member
                        break
                
                if not ticket_owner:
                    ticket_owner = interaction.user

                # ----- Transcript 대화록 생성 코드는 그대로 유지 -----
                await channel.send("💾 대화 내용을 안전하게 백업하는 중입니다...")
                
                transcript_text = f"=== {channel_name} 티켓 대화록 ===\n"
                transcript_text += f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                transcript_text += f"티켓 소유자: {ticket_owner.name} ({ticket_owner.id})\n"
                transcript_text += f"티켓 종료자: {interaction.user.name}\n"
                transcript_text += "=========================================\n\n"

                async for msg in channel.history(limit=1000, oldest_first=True):
                    if msg.author == bot.user and (msg.embeds or "삭제됩니다" in msg.content or "백업하는 중" in msg.content):
                        continue
                    time_str = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
                    content = msg.content if msg.content else "[사진/파일 또는 임베드 메시지]"
                    transcript_text += f"[{time_str}] {msg.author.name}: {content}\n"

                file_data = BytesIO(transcript_text.encode('utf-8'))
                transcript_file = discord.File(fp=file_data, filename=f"transcript-{channel_name}.txt")
                
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    log_embed = discord.Embed(
                        title="📄 티켓 대화록 (Transcript) 백업 완료",
                        description=f"**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}\n**종료자:** {interaction.user.mention}",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    await log_channel.send(embed=log_embed, file=transcript_file)
                # --------------------------------------------------

                await channel.send(f"⚠️ 백업 완료! 채널이 5초 후에 삭제되며, {ticket_owner.mention}님에게 프리미엄 후기 요청 DM이 발송됩니다.")
                
                # ----- 화려하게 디자인된 DM 발송 섹션 -----
                try:
                    dm_embed = discord.Embed(
                        title="👑 커미션 이용 만족도 조사",
                        description=(
                            f"안녕하세요, {ticket_owner.mention}님! 이용해주신 티켓이 종료되었습니다.\n\n"
                            "보내드린 작업물은 마음에 드셨을까요? 마음에 드셨다면 "
                            "**아래 버튼을 터치하여 즉시 별점 후기**를 남겨주실 수 있습니다.\n\n"
                            "별점을 선택하시면 서버 내 후기 게시판에 자동으로 기록됩니다 ✨"
                        ),
                        color=0xFEE75C # 골드 색상
                    )
                    dm_embed.add_field(name="📌 안내사항", value="개인 DM 차단이 켜져 있는 유저는 별점 반영이 안 될 수 있으니 주의해 주세요.", inline=False)
                    dm_embed.set_footer(text="단 1초의 피드백이 저에게 아주 큰 성장 동력이 됩니다. 감사합니다 ❤️")
                    
                    # 별점 5점 만점짜리 버튼 뷰를 실어서 전송
                    await ticket_owner.send(embed=dm_embed, view=StarRatingView(guild, ticket_owner))
                    print(f"[디자인 DM 발송 완료] {ticket_owner.name}님에게 만족도 조사 전송함.")
                except discord.Forbidden:
                    print(f"[DM 차단됨] {ticket_owner.name}님이 DM을 잠가두어 발송 실패.")
                except Exception as e:
                    print(f"[DM 에러] {e}")

                await asyncio.sleep(5)
                await channel.delete()
            else:
                await interaction.response.send_message("여기는 티켓 채널이 아닙니다!", ephemeral=True)
        except Exception as e:
            print(f"[티켓 닫기 에러] {e} - 봇 유지")


# 3. 티켓 열기 버튼 정의 (유저가 문의하기 누를 때 작동)
class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 티켓 열기", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            guild = interaction.guild
            user = interaction.user

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False), 
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True), 
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True) 
            }

            ticket_channel = await guild.create_text_channel(name=f"티켓-{user.name}", overwrites=overwrites)
            await interaction.response.send_message(f"{ticket_channel.mention} 채널이 생성되었습니다!", ephemeral=True)

            embed = discord.Embed(
                title="🎫 1:1 문의 및 커미션 상담", 
                description=(
                    f"안녕하세요, {user.mention}님!\n커미션 신청 및 상담하실 내용을 아래에 자유롭게 남겨주세요.\n\n"
                    "⚠️ 상담 및 작업이 완료되어 정산까지 끝나면 아래 **[🔒 티켓 닫기]** 버튼을 눌러주세요."
                ), 
                color=0x00ff00
            )
            await ticket_channel.send(embed=embed, view=TicketCloseView())
        except Exception as e:
            print(f"[티켓 열기 에러] {e} - 봇 유지")

# =========================================================================


@bot.event
async def on_message(message: discord.Message):
    try:
        if message.author == bot.user:
            return
        await bot.process_commands(message)
    except Exception as general_error:
        print(f"[메시지 에러] {general_error} - 봇 유지")


# ⚙️ 관리자용 티켓 설치 패널 명령어
@bot.command()
@commands.has_permissions(administrator=True) 
async def 티켓생성(ctx):
    embed = discord.Embed(
        title="✉️ 고객 센터 / 문의하기", 
        description="도움이 필요하시거나 커미션 신청을 원하시면 아래 버튼을 눌러주세요!\n관리자와 1:1로 대화할 수 있는 비밀 티켓 채널이 열립니다.", 
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=TicketOpenView())


@bot.event
async def on_ready():
    # 봇이 꺼졌다 켜져도 과거에 생성된 버튼들이 완벽하게 작동하도록 영구 패널 뷰 등록
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())
    # 주의: 별점 뷰는 다이내믹하게 생성되므로 영구 프리셋 캐시 구조 제외 가능하나 기본 컴포넌트 데이터 등록 유지
    print(f"프리미엄 5점만점 별점 시스템 작동 시작: {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("봇이 수동으로 종료되었습니다.")
