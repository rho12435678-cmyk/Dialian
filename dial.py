import discord
from discord.ext import commands
import os
import asyncio
from io import BytesIO
from datetime import datetime

TOKEN = os.getenv("TOKEN")

REVIEW_CHANNEL_NAME = "후기"
LOG_CHANNEL_NAME = "구매로그"        # 손님들도 보는 공개 로그 채널

# 알림을 받을 개발자(관리자)들의 디스코드 고유 ID 리스트
DEVELOPER_IDS = [1292859064065458189, 1468584582113919129, 1465051418162626763, 859756809865789451] 

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


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
                embed_desc = interaction.message.embeds[0].description
                for g in bot.guilds:
                    if discord.utils.get(g.text_channels, name=REVIEW_CHANNEL_NAME):
                        guild = g
                        break
            
            if not guild:
                guild = bot.guilds[0] if bot.guilds else None

            if not guild:
                return await interaction.followup.send("연동된 서버를 찾을 수 없습니다.", ephemeral=True)

            review_channel = discord.utils.get(guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if not review_channel:
                review_channel = next((ch for ch in guild.text_channels if "후기" in ch.name), None)

            if review_channel:
                star_emojis = "⭐" * stars
                
                review_embed = discord.Embed(
                    title="✨ 소중한 커미션 후기가 도착했습니다!",
                    color=0xFEE75C,
                    timestamp=datetime.now()
                )
                review_embed.set_thumbnail(url=ticket_owner.display_avatar.url)
                review_embed.add_field(name="👤 작성자(오너)", value=f"{ticket_owner.mention} ({ticket_owner.name})", inline=True)
                review_embed.add_field(name="📊 만족도 별점", value=f"**{star_emojis} ({stars} / 5점)**", inline=True)
                review_embed.set_footer(text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏")

                await review_channel.send(embed=review_embed)

                success_view = discord.ui.View()
                try:
                    invite = await review_channel.create_invite(max_age=300, max_uses=1)
                    success_view.add_item(discord.ui.Button(label="😄 내가 쓴 후기 보러가기", url=invite.url, style=discord.ButtonStyle.link))
                except:
                    pass

                await interaction.followup.send(
                    f"🎉 성공적으로 **{stars}점** 별점이 제출되었습니다! 소중한 의견 감사합니다.", 
                    view=success_view, 
                    ephemeral=True
                )
                
                disabled_view = discord.ui.View()
                for i in range(1, 6):
                    style = discord.ButtonStyle.success if i == 5 else discord.ButtonStyle.secondary
                    disabled_view.add_item(discord.ui.Button(
                        label=f"⭐ {i}점", 
                        style=style, 
                        custom_id=f"star_{i}",
                        disabled=True
                    ))
                await interaction.message.edit(view=disabled_view)
            else:
                await interaction.followup.send("서버의 후기 채널을 찾을 수 없습니다. 관리자에게 문의해 주세요.", ephemeral=True)
        except Exception as e:
            print(f"[별점 등록 에러] {e}")


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
                
                ticket_owner = None
                try:
                    owner_id = int(channel_name.split("-")[-1])
                    ticket_owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
                except Exception:
                    ticket_owner = interaction.user

                await interaction.followup.send("💾 대화 내용을 안전하게 백업하는 중입니다...")
                
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

                file_data_1 = BytesIO(transcript_text.encode('utf-8'))
                file_data_2 = BytesIO(transcript_text.encode('utf-8'))
                
                transcript_file_dm = discord.File(fp=file_data_1, filename=f"transcript-{channel_name}.txt")
                transcript_file_ch = discord.File(fp=file_data_2, filename=f"transcript-{channel_name}.txt")
                
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    public_embed = discord.Embed(
                        title="🔒 티켓 종료 알림",
                        description=f"**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}\n**종료자:** {interaction.user.mention}\n\n*이용해 주셔서 감사합니다!*",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    await log_channel.send(content=f"🔒 {ticket_owner.mention} 님의 티켓이 닫혔습니다.", embed=public_embed)
                    
        except Exception as e:
            print(f"[티켓 닫기 에러] {e}")

# ==================== [봇 초기화 및 구동 시스템 추가] ====================

@bot.event
async def on_ready():
    print(f"🚀 로그인 성공: {bot.user.name} ({bot.user.id})")
    print("--------------------------------------------------")

# 봇이 최초 시작될 때 버튼 컴포넌트(View)를 디스코드에 상시 대기 상태로 등록합니다.
# 이 처리를 해두어야 봇이 재부팅되어도 기존에 떠 있던 버튼들이 정상 작동합니다.
@bot.event
async def setup_hook():
    bot.add_view(StarRatingView())
    bot.add_view(TicketCloseView())
    print("✨ 영속성 버튼(StarRatingView, TicketCloseView) 등록 완료!")

# ⚠️ [핵심] 봇을 실제로 실행시키는 명령어입니다. (가장 아래에 위치해야 함)
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ 에러: 환경 변수에서 'TOKEN'을 찾을 수 없습니다. Railway의 Variables 설정을 확인하세요.")
