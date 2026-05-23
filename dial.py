import discord
from discord.ext import commands
import os
import asyncio
from io import BytesIO
from datetime import datetime

TOKEN = os.getenv("TOKEN")

# 채널 이름 및 관리자 고유 ID 설정
REVIEW_CHANNEL_NAME = "후기"
LOG_CHANNEL_NAME = "구매로그"       

# 알림을 받을 개발자(관리자)들의 디스코드 고유 ID 리스트
DEVELOPER_IDS = [1292859064065458189, 1468584582113919129, 1465051418162626763, 859756809865789451] 


intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ==================== [자동 별점 및 후기 View 시스템] ====================

class StarRatingView(discord.ui.View):
    def __init__(self):
        # 영속성(Persistent) 뷰 유지를 위해 timeout=None 설정
        super().__init__(timeout=None)

    # 💡 [핵심 수정] interaction_check 대신, 디스코드 컴포넌트 시스템에 등록된 모든 버튼의 이벤트를 직접 핸들링
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("star_"):
            try:
                parts = custom_id.split("_")
                stars = int(parts[1])
                guild_id = int(parts[2])
                owner_id = int(parts[3])
                
                # 별점 처리 로직 실행
                await self.process_rating(interaction, stars, guild_id, owner_id)
            except Exception as e:
                print(f"[버튼 파싱 에러] {e}")
                await interaction.response.send_message("유효하지 않은 버튼 요청이거나 데이터가 만료되었습니다.", ephemeral=True)
            return False 
        return True

    async def process_rating(self, interaction: discord.Interaction, stars: int, guild_id: int, owner_id: int):
        try:
            # 상호작용 처리가 늦어질 수 있으므로 먼저 지연 처리
            await interaction.response.defer(ephemeral=True)

            guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
            if not guild:
                return await interaction.followup.send("해당 서버를 찾을 수 없습니다.", ephemeral=True)

            ticket_owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
            
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
                if ticket_owner:
                    review_embed.set_thumbnail(url=ticket_owner.display_avatar.url)
                    review_embed.add_field(name="👤 작성자(오너)", value=f"{ticket_owner.mention} ({ticket_owner.name})", inline=True)
                else:
                    review_embed.add_field(name="👤 작성자(오너)", value=f"알 수 없는 유저 (ID: {owner_id})", inline=True)
                    
                review_embed.add_field(name="📊 만족도 별점", value=f"**{star_emojis} ({stars} / 5점)**", inline=True)
                review_embed.set_footer(text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏")

                # 후기 채널에 전송
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
                
                # 버튼 비활성화 (해당 메시지를 찾아서 업데이트)
                try:
                    disabled_view = discord.ui.View()
                    for i in range(1, 6):
                        style = discord.ButtonStyle.success if i == 5 else discord.ButtonStyle.secondary
                        disabled_view.add_item(discord.ui.Button(
                            label=f"⭐ {i}점", 
                            style=style, 
                            custom_id=f"star_{i}_{guild_id}_{owner_id}",
                            disabled=True
                        ))
                    await interaction.message.edit(view=disabled_view)
                except Exception as edit_err:
                    print(f"[메시지 버튼 비활성화 실패] {edit_err}")
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

                file_data_1 = BytesIO(transcript_text.encode('utf-8'))
                file_data_2 = BytesIO(transcript_text.encode('utf-8'))
                
                transcript_file_dm = discord.File(fp=file_data_1, filename=f"transcript-{channel_name}.txt")
                transcript_file_ch = discord.File(fp=file_data_2, filename=f"transcript-{channel_name}.txt")
                
                # 1. 구매로그 알림
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    public_embed = discord.Embed(
                        title="🔒 티켓 종료 알림",
                        description=f"**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}\n**종료자:** {interaction.user.mention}\n\n*이용해 주셔서 감사합니다!*",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    await log_channel.send(content=f"🔒 {ticket_owner.mention} 님의 티켓이 닫혔습니다.", embed=public_embed)

                # 2. 관리자 비밀 로그
                admin_log_channel = discord.utils.get(guild.text_channels, name=ADMIN_LOG_CHANNEL_NAME)
                if admin_log_channel:
                    admin_ch_embed = discord.Embed(
                        title="📄 [보안 서버 백업] 상세 대화록",
                        description=f"**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}\n**종료자:** {interaction.user.mention}",
                        color=discord.Color.red(),
                        timestamp=datetime.now()
                    )
                    await admin_log_channel.send(embed=admin_ch_embed, file=transcript_file_ch)

                # 3. 대표 관리자 DM 백업
                try:
                    admin_user = await bot.fetch_user(ADMIN_USER_ID)
                    admin_embed = discord.Embed(
                        title="📄 [보안 DM 백업] 상세 대화록",
                        description=f"**서버:** `{guild.name}`\n**채널명:** `{channel_name}`\n**티켓 오너:** {ticket_owner.mention}",
                        color=discord.Color.dark_red(),
                        timestamp=datetime.now()
                    )
                    await admin_user.send(embed=admin_embed, file=transcript_file_dm)
                except Exception as dm_err:
                    print(f"[보안 백업 실패] 관리자 DM 발송 실패: {dm_err}")

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
                    
                    dynamic_star_view = discord.ui.View(timeout=None)
                    for i in range(1, 6):
                        style = discord.ButtonStyle.success if i == 5 else discord.ButtonStyle.secondary
                        dynamic_star_view.add_item(discord.ui.Button(
                            label=f"⭐ {i}점", 
                            style=style, 
                            custom_id=f"star_{i}_{guild.id}_{ticket_owner.id}"
                        ))
                    
                    await ticket_owner.send(embed=dm_embed, view=dynamic_star_view)
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

            ticket_channel = await guild.create_text_channel(name=f"티켓-{user.name}-{user.id}", overwrites=overwrites)
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

            log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
            if log_channel:
                open_log_embed = discord.Embed(
                    title="📩 티켓 생성 알림",
                    description=f"**생성 채널:** {ticket_channel.mention}\n**생성 유저:** {user.mention} ({user.name})",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                await log_channel.send(content=f"📩 {user.mention} 님이 새로운 티켓을 열었습니다.", embed=open_log_embed)

            try:
                dev_embed = discord.Embed(
                    title="🔔 [티켓 오픈] 새로운 문의가 접수되었습니다!",
                    description=f"**서버 이름:** {guild.name}\n**생성된 채널:** {ticket_channel.mention}",
                    color=0x5865F2,
                    timestamp=datetime.now()
                )
                dev_embed.add_field(name="👤 신청자(손님)", value=f"{user.mention} ({user.name})", inline=True)
                dev_embed.set_footer(text="빠른 확인 부탁드립니다! 🚀")

                direct_jump_url = f"https://discord.com/channels/{guild.id}/{ticket_channel.id}"
                
                dev_view = discord.ui.View()
                dev_view.add_item(discord.ui.Button(label="🚀 생성된 티켓으로 즉시 이동", url=direct_jump_url, style=discord.ButtonStyle.link))

                for dev_id in DEVELOPER_IDS:
                    try:
                        dev_user = await bot.fetch_user(dev_id)
                        await dev_user.send(embed=dev_embed, view=dev_view)
                    except:
                        pass
            except Exception as total_dm_err:
                print(f"[개발자 알림 총괄 에러] {total_dm_err}")
        except Exception as e:
            print(f"[티켓 열기 에러] {e} - 봇 유지")


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)


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
    # 1. 일반 고정형 버튼 등록
    bot.add_view(TicketOpenView())
    bot.add_view(TicketCloseView())
    
    # 2. 🔥 [가장 중요] 재부팅 시 수많은 유저 DM에 뿌려져 있는 별점 컴포넌트들을 통째로 감시하는 영속성 뼈대 View 등록
    # 봇이 기억해야 할 custom_id의 패턴(Prefix)을 가진 더미 뷰를 생성하여 주입합니다.
    persistent_star_view = StarRatingView()
    for i in range(1, 6):
        # 0, 0은 일종의 와일드카드용 더미 서픽스입니다. interaction_check에서 split해서 동적으로 파싱하게 됩니다.
        style = discord.ButtonStyle.success if i == 5 else discord.ButtonStyle.secondary
        persistent_star_view.add_item(discord.ui.Button(
            label=f"⭐ {i}점", 
            style=style, 
            custom_id=f"star_{i}_0_0"
        ))
    bot.add_view(persistent_star_view)
    
    print(f"프리미엄 5점만점 영속성 별점 레이더 작동 시작: {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("봇이 수동으로 종료되었습니다.")
