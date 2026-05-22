import discord
from discord.ext import commands
import os
import asyncio
from io import BytesIO
from datetime import datetime

TOKEN = os.getenv("TOKEN")

# 채널 이름 및 관리자 고유 ID 설정
REVIEW_CHANNEL_NAME = "후기"
LOG_CHANNEL_NAME = "구매로그"  # 손님들도 보는 로그 채널 (텍스트 파일 제외하고 알림만 전송됨)
ADMIN_USER_ID = 123456789012345678  # 여기에 본인의 디스코드 유저 ID(숫자)를 입력하세요.

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== [자동 별점 및 후기 View 시스템] ====================

class StarRatingView(discord.ui.View):
    def __init__(self, guild, ticket_owner):
        super().__init__(timeout=None)
        self.guild = guild
        self.ticket_owner = ticket_owner

    async def process_rating(self, interaction: discord.Interaction, stars: int):
        try:
            review_channel = discord.utils.get(self.guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if not review_channel:
                review_channel = next((ch for ch in self.guild.text_channels if "후기" in ch.name), None)

            if review_channel:
                star_emojis = "⭐" * stars
                
                review_embed = discord.Embed(
                    title="✨ 소중한 커미션 후기가 도착했습니다!",
                    color=0xFEE75C,
                    timestamp=datetime.now()
                )
                review_embed.set_thumbnail(url=self.ticket_owner.display_avatar.url)
                review_embed.add_field(name="👤 작성자(오너)", value=f"{self.ticket_owner.mention} ({self.ticket_owner.name})", inline=True)
                review_embed.add_field(name="📊 만족도 별점", value=f"**{star_emojis} ({stars} / 5점)**", inline=True)
                review_embed.set_footer(text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏")

                await review_channel.send(embed=review_embed)

                success_view = discord.ui.View()
                try:
                    invite = await review_channel.create_invite(max_age=300, max_uses=1)
                    success_view.add_item(discord.ui.Button(label="😄 내가 쓴 후기 보러가기", url=invite.url, style=discord.ButtonStyle.link))
                except:
                    pass

                await interaction.response.send_message(
                    f"🎉 성공적으로 **{stars}점** 별점이 제출되었습니다! 소중한 의견 감사합니다.", 
                    view=success_view, 
                    ephemeral=True
                )
                
                self.stop()
            else:
                await interaction.response.send_message("서버의 후기 채널을 찾을 수 없습니다. 관리자에게 문의해 주세요.", ephemeral=True)
        except Exception as e:
            print(f"[별점 등록 에러] {e}")

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
                
                target_username = channel_name.replace("티켓-", "").lower()
                ticket_owner = None
                
                for member in guild.members:
                    if member.name.lower() == target_username:
                        ticket_owner = member
                        break
                
                if not ticket_owner:
                    ticket_owner = interaction.user

                await channel.send("💾 대화 내용을 안전하게 백업하는 중입니다...")
                
                # ----- [상세 대화록 텍스트 데이터 생성] -----
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
                
                # 1. [손님 전용 공개 로그] 대화 내용 파일(.txt)을 제외하고, '종료 알림 임베드'만 전송
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    public_embed = discord.Embed(
                        title="🔒 티켓 종료 알림",
                        description=f"**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}\n**종료자:** {interaction.user.mention}\n\n*이용해 주셔서 감사합니다!*",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    await log_channel.send(embed=public_embed)  # 파일(file=...)을 넣지 않아 안전합니다.

                # 2. [관리자 전용 비밀 로그] 계좌번호 등 민감한 내용이 담긴 텍스트 파일은 오직 관리자 DM으로만 전송
                try:
                    admin_user = await bot.fetch_user(ADMIN_USER_ID)
                    admin_embed = discord.Embed(
                        title="📄 [보안 백업] 상세 대화록",
                        description=f"**서버:** `{guild.name}`\n**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}",
                        color=discord.Color.dark_red(),
                        timestamp=datetime.now()
                    )
                    await admin_user.send(embed=admin_embed, file=transcript_file)
                    print(f"[보안 백업 완료] 관리자 DM으로 대화록 전송.")
                except Exception as dm_err:
                    print(f"[보안 백업 실패] 관리자 DM을 보낼 수 없습니다: {dm_err}")

                # --------------------------------------------------

                await channel.send(f"⚠️ 백업 완료! 채널이 5초 후에 삭제되며, {ticket_owner.mention}님에게 프리미엄 후기 요청 DM이 발송됩니다.")
                
                try:
                    dm_embed = discord.Embed(
                        title="👑 커미션 이용 만족도 조사",
                        description=(
                            f"안녕하세요, {ticket_owner.mention}님! 이용해주신 티켓이 종료되었습니다.\n\n"
                            "보내드린 작업물은 마음에 드셨을까요? 마음에 드셨다면 "
                            "**아래 버튼을 터치하여 즉시 별점 후기**를 남겨주실 수 있습니다.\n\n"
                            "별점을 선택하시면 서버 내 후기 게시판에 자동으로 기록됩니다 ✨"
                        ),
                        color=0xFEE75C
                    )
                    dm_embed.add_field(name="📌 안내사항", value="개인 DM 차단이 켜져 있는 유저는 별점 반영이 안 될 수 있으니 주의해 주세요.", inline=False)
                    dm_embed.set_footer(text="단 1초의 피드백이 저에게 아주 큰 성장 동력이 됩니다. 감사합니다 ❤️")
                    
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


@bot.event
async def on_message(message: discord.Message):
    try:
        if message.author == bot.user:
            return
        await bot.process_commands(message)
    except Exception as general_error:
        print(f"[메시지 에러] {general_error} - 봇 유지")


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
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())
    print(f"프리미엄 5점만점 별점 시스템 작동 시작: {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("봇이 수동으로 종료되었습니다.")
