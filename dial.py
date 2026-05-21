import discord
from discord.ext import commands
import os
import asyncio

TOKEN = os.getenv("TOKEN")

# 후기(리뷰)가 작성될 디스코드 채널 이름
REVIEW_CHANNEL_NAME = "😄후기"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== [티켓 및 DM 자동화 시스템 View] ====================

# 1. 티켓 닫기 버튼 정의 (닫힐 때 방 주인에게 즉시 DM 발송)
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Railway에서 24시간 버튼이 안 죽고 계속 대기

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channel_name = interaction.channel.name
            
            # 현재 채널이 티켓 채널이 맞는지 검사 (엉뚱한 일반 채널 폭파 방지)
            if "티켓-" in channel_name:
                await interaction.response.defer() # 봇 버퍼링(렉) 방지
                
                # 채널 이름(티켓-유저이름)에서 유저 이름을 추출하여 서버에서 해당 오너(멤버) 찾기
                target_username = channel_name.replace("티켓-", "").lower()
                ticket_owner = None
                
                for member in interaction.guild.members:
                    if member.name.lower() == target_username:
                        ticket_owner = member
                        break
                
                # 만약 디스코드 이름 변경 등으로 못 찾았다면 버튼을 누른 사람에게 발송 (안전장치)
                if not ticket_owner:
                    ticket_owner = interaction.user

                # 1. 채널 삭제 안내 메시지 전송
                await interaction.channel.send(f"⚠️ 이 티켓 채널이 5초 후에 삭제되며, {ticket_owner.mention}님에게 후기 요청 DM이 발송됩니다.")
                
                # 2. 방 주인(오너)에게 즉시 DM으로 리뷰 요청 발송
                try:
                    embed = discord.Embed(
                        title="⭐ 커미션을 이용해주셔서 감사합니다!",
                        description=(
                            f"안녕하세요, {ticket_owner.mention}님! 이용하신 티켓이 종료되었습니다.\n\n"
                            "작업물은 마음에 드셨나요? 만족하셨다면 아래 버튼을 눌러\n"
                            "간단한 후기를 남겨주시면 저에게 정말 큰 힘이 됩니다 🙏"
                        ),
                        color=discord.Color.gold()
                    )
                    # 중요: 후기 채널로 바로 순간이동하는 버튼을 함께 실어서 DM 전송
                    await ticket_owner.send(embed=embed, view=ReviewView(interaction.guild))
                    print(f"[티켓 종료 자동완료] {ticket_owner.name}님에게 후기 DM 발송 성공.")
                except discord.Forbidden:
                    print(f"[DM 차단됨] {ticket_owner.name}님이 개인 DM을 차단해두어 발송에 실패했습니다.")
                except Exception as e:
                    print(f"[DM 발송 중 에러] {e}")

                # 3. 5초 대기 후 티켓 채널 삭제
                await asyncio.sleep(5)
                await interaction.channel.delete()
            else:
                await interaction.response.send_message("여기는 티켓 채널이 아닙니다!", ephemeral=True)
        except Exception as e:
            print(f"[티켓 닫기 에러] {e} - 봇 유지")


# 2. 티켓 열기 버튼 정의 (유저가 문의하기 누를 때 작동)
class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 티켓 열기", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            guild = interaction.guild
            user = interaction.user

            # 티켓 채널 권한 설정 (문의한 유저와 봇, 관리자만 보이게 차단)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False), # 일반 유저 차단
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True), # 문의 유저 허용
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True) # 봇 허용
            }

            # 채널명을 '티켓-유저이름' 형식으로 생성 (나중에 닫을 때 주인을 역추적하기 위함)
            ticket_channel = await guild.create_text_channel(name=f"티켓-{user.name}", overwrites=overwrites)
            
            # 버튼 누른 유저에게만 성공 메시지 띄우기
            await interaction.response.send_message(f"{ticket_channel.mention} 채널이 생성되었습니다!", ephemeral=True)

            embed = discord.Embed(
                title="🎫 1:1 문의 및 커미션 상담", 
                description=(
                    f"안녕하세요, {user.mention}님!\n커미션 신청 및 상담하실 내용을 아래에 자유롭게 남겨주세요.\n\n"
                    "⚠️ 상담 및 작업이 완료되어 정산까지 끝나면 아래 **[🔒 티켓 닫기]** 버튼을 눌러주세요."
                ), 
                color=0x00ff00
            )
            # 생성된 비밀 채널 안에 [🔒 티켓 닫기] 버튼을 함께 전송
            await ticket_channel.send(embed=embed, view=TicketCloseView())
            print(f"[티켓 개설] {user.name}님의 문의 방이 열렸습니다.")

        except Exception as e:
            print(f"[티켓 열기 에러] {e} - 봇 유지")

# =========================================================================


# 3. 유저가 DM에서 누를 [리뷰 작성하러 가기] 버튼 정의
class ReviewView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=None)
        self.guild = guild

    @discord.ui.button(label="리뷰 작성하러 가기", style=discord.ButtonStyle.green, custom_id="go_to_review")
    async def review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            review_channel = discord.utils.get(self.guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if not review_channel:
                review_channel = next((ch for ch in self.guild.text_channels if "후기" in ch.name), None)

            if review_channel:
                await review_channel.send(
                    f"{interaction.user.mention} 님, 최근 커미션 후기를 아래에 작성 부탁드립니다 ⭐"
                )
                await interaction.response.send_message(
                    "리뷰 채널로 안내되었습니다. 감사합니다!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "리뷰 채널을 찾을 수 없습니다. 서버 관리자에게 문의해 주세요.",
                    ephemeral=True
                )
        except Exception as e:
            print(f"[리뷰이동 버튼 에러] {e} - 봇 유지")


@bot.event
async def on_message(message: discord.Message):
    try:
        if message.author == bot.user:
            return

        # 프리픽스 명령어(!티켓생성 등) 처리 활성화
        await bot.process_commands(message)
                    
    except Exception as general_error:
        print(f"[메시지 에러] {general_error} - 봇 유지")


# ⚙️ 관리자용 티켓 설치 패널 명령어
@bot.command()
@commands.has_permissions(administrator=True) # 서버 관리자 권한을 가진 사람만 사용 가능
async def 티켓생성(ctx):
    embed = discord.Embed(
        title="✉️ 고객 센터 / 문의하기", 
        description="도움이 필요하시거나 커미션 신청을 원하시면 아래 버튼을 눌러주세요!\n관리자와 1:1로 대화할 수 있는 비밀 티켓 채널이 열립니다.", 
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=TicketOpenView())


@bot.event
async def on_ready():
    # 봇이 재부팅되어도 예전에 켜둔 버튼들이 영구적으로 작동하도록 컴포넌트 지속성 등록
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())
    bot.add_view(ReviewView(None))
    print(f"티켓 마스터 올인원 봇 작동 시작: {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("봇이 수동으로 종료되었습니다.")
