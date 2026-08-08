import discord
import aiosqlite
from datetime import datetime
from config import *
from database.database import DATABASE
from database.views.close_ticket import TicketCloseView
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView
from database.views.claim_view import ClaimTicketView
from database.ticket_notice import build_ticket_notice_embed
from database.purchase_log import send_purchase_log
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)


class PurchaseModal(discord.ui.Modal):
    COMMISSION_NAME = "GFX"

    def __init__(self, bundle_type: str = "단품 (1개)", selected_designer: int = None):
        super().__init__(title=f"🎨 GFX 커미션 신청서 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer

        # 1. Roblox 닉네임
        self.roblox_nickname = discord.ui.TextInput(
            label="🎮 Roblox 닉네임",
            placeholder="작품에 반영될 로블록스 닉네임을 작성해주세요.",
            required=True,
            max_length=30
        )
        self.add_item(self.roblox_nickname)

        # 2. GFX 장르
        self.gfx_genre = discord.ui.TextInput(
            label="🎬 원하는 GFX 장르",
            placeholder="예: 밀리터리, 판타지, 일상, SF 등",
            required=True,
            max_length=100
        )
        self.add_item(self.gfx_genre)

        # 3. 묶음 종류에 따른 본품/보너스 요구사항 입력칸 분리
        if self.bundle_type == "2+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~2번째 작품 상세 요구사항",
                placeholder="1, 2번째 작품에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 3번째 작품 요구사항 (2+1 보너스)",
                placeholder="3번째(보너스) 작품에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.fourth_style)

        elif self.bundle_type == "3+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~3번째 작품 상세 요구사항",
                placeholder="1, 2, 3번째 작품에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 4번째 작품 요구사항 (3+1 보너스)",
                placeholder="4번째(보너스) 작품에 대한 요구사항을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.fourth_style)

        else:  # 단품 (1개)
            self.gfx_style = discord.ui.TextInput(
                label="📝 원하는 스타일 및 설명",
                placeholder="원하시는 콘셉트, 구도, 색감, 의상 등을 적어주세요.",
                required=True, style=discord.TextStyle.paragraph, max_length=1000
            )
            self.add_item(self.gfx_style)
            self.fourth_style = None

    async def on_submit(self, interaction: discord.Interaction):
        ticket_lock = await acquire_ticket_creation_lock(interaction)
        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)
        except Exception as error:
            print(f"[{self.COMMISSION_NAME} ticket creation failed] {error}")
            await interaction.response.send_message("❌ 티켓 생성 중 오류가 발생했습니다.", ephemeral=True)
        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # 디자이너 배정 여부에 따른 텍스트 및 Claim View 준비
        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)
            designer_name = developer.mention if developer else "미지정"
            claim_view = ClaimTicketView(is_claimed=True)   # 이미 배정됨 -> 버튼 비활성화
        else:
            designer_name = "미지정"
            claim_view = ClaimTicketView(is_claimed=False)  # 미배정 -> [내가 담당하기] 활성화

        if get_open_ticket_channel(guild, user):
            return await interaction.response.send_message("❌ 이미 진행 중인 티켓이 있습니다.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # 티켓 채널 권한 제어
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, view_channel=False),
            user: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, manage_channels=True)
        }

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)
            if developer:
                overwrites[developer] = discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True)

        ticket_channel = await guild.create_text_channel(
            name=f"티켓-{user.id}",
            overwrites=overwrites,
            topic=str(user.id)
        )

        # DB 저장 로직
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                INSERT INTO commissions(ticket_channel, customer_id, designer_id, category, status, progress, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticket_channel.id, user.id, self.selected_designer, self.COMMISSION_NAME, "in_progress", 0, datetime.now().isoformat(), datetime.now().isoformat()))
            await db.commit()

        # 신청서 임베드 생성
        embed = discord.Embed(title=f"📋 {self.COMMISSION_NAME} 신청서 ({self.bundle_type})", color=0x5865F2, timestamp=datetime.now())
        embed.add_field(name="👨‍💻 담당 디자이너", value=designer_name, inline=False)
        if self.roblox_nickname and self.roblox_nickname.value:
            embed.add_field(name="🎮 Roblox 닉네임", value=self.roblox_nickname.value, inline=False)
        if self.gfx_genre and self.gfx_genre.value:
            embed.add_field(name="🎬 GFX 장르", value=self.gfx_genre.value, inline=False)
        embed.add_field(name="🎨 요구사항", value=self.gfx_style.value, inline=False)
        
        # 보너스 요구사항이 있는 경우 (2+1 또는 3+1)
        if self.fourth_style:
            bonus_title = "🎁 3번째 작품 요구사항 (2+1 보너스)" if self.bundle_type == "2+1 묶음" else "🎁 4번째 작품 요구사항 (3+1 보너스)"
            embed.add_field(name=bonus_title, value=self.fourth_style.value, inline=False)

        await ticket_channel.send(content=f"{user.mention}\n신청이 접수되었습니다.", embed=embed)

        # 안내, 참고자료 전송
        guide_embed = build_ticket_notice_embed() 
        
        ref_embed = discord.Embed(
            title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
            description=f"{user.mention}님, 원하시는 구도, 분위기, 색감, 참고용 이미지/파일을 구체적으로 올려주세요!",
            color=0x5865F2
        )
        ref_embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")

        await ticket_channel.send(embeds=[guide_embed, ref_embed])

        # 진행 임베드 + 담당하기 버튼 전송
        progress_embed = discord.Embed(
            title="📌 커미션 진행",
            description=(f"👨‍💻 담당 디자이너 : {designer_name}\n\n📌 상태 : 🟢 상담중\n📊 진행률 : 0%\n⏰ 예상 완료 : 미설정"),
            color=discord.Color.green(),
            timestamp=datetime.now()
        )

        progress_message = await ticket_channel.send(embed=progress_embed, view=claim_view)

        # 구매 로그 전송
        log_channel = discord.utils.get(
            guild.text_channels,
            name=LOG_CHANNEL_NAME
        )
        if log_channel:
            await send_purchase_log(guild, content=(
                f"📩 새로운 {self.COMMISSION_NAME} 티켓 생성\n"
                f"{ticket_channel.mention}\n"
                f"신청자 : {user.mention}"
            ))

        # 담당 디자이너에게 DM으로 관리 버튼 개별 발송 (오류 분리 처리)
        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                dm_blocked = False

                # 1. 알림 메시지 발송
                try:
                    await developer.send(
                        f"🔔 새로운 커미션이 들어왔습니다.\n{ticket_channel.mention}"
                    )
                except Exception as e:
                    print(f"[DM 1단계 전송 실패 - DM 차단 가능성] {e}")
                    dm_blocked = True

                if not dm_blocked:
                    # 2. 진행률 관리 버튼 발송
                    try:
                        await developer.send(
                            f"📊 진행률 관리\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                            view=ProgressView(progress_message, self.selected_designer)
                        )
                    except Exception as e:
                        print(f"[DM 2단계(ProgressView) 전송 에러] {e}")

                    # 3. 결제 및 티켓 관리 버튼 발송
                    try:
                        await developer.send(
                            f"💳 결제 및 티켓 관리\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                            view=PaymentView(ticket_channel, self.selected_designer)
                        )
                    except Exception as e:
                        print(f"[DM 3단계(PaymentView) 전송 에러] {e}")

                    # 4. 티켓 종료/삭제 버튼 발송
                    try:
                        await developer.send(
                            f"🔒 티켓 종료 / 🗑️ 티켓 삭제\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                            view=TicketCloseView(ticket_channel)
                        )
                    except Exception as e:
                        print(f"[DM 4단계(TicketCloseView) 전송 에러] {e}")

                # 첫 DM 자체가 차단되어 아예 안 들어간 경우에만 백업 안내문 출력
                else:
                    await ticket_channel.send(
                        f"{developer.mention} DM 전송에 실패하여 티켓에 관리 버튼을 전송합니다.",
                        allowed_mentions=discord.AllowedMentions(users=True)
                    )
                    await ticket_channel.send(
                        "📊 진행률 관리",
                        view=ProgressView(progress_message, self.selected_designer)
                    )
                    await ticket_channel.send(
                        "💳 결제 및 티켓 관리",
                        view=PaymentView(ticket_channel, self.selected_designer)
                    )
                    await ticket_channel.send(
                        "🔒 티켓 종료 / 🗑️ 티켓 삭제",
                        view=TicketCloseView(ticket_channel)
                    )
            else:
                print(f"[DM 전송 실패] 서버에서 디자이너를 찾지 못했습니다: {self.selected_designer}")

        await interaction.followup.send(f"✅ 신청 완료!\n{ticket_channel.mention}", ephemeral=True)


class UniformModal(PurchaseModal):
    COMMISSION_NAME = "Roblox 복장"

    def __init__(self, bundle_type: str = "단품 (1개)", selected_designer: int = None):
        discord.ui.Modal.__init__(self, title=f"👕 복장 커미션 신청서 [{bundle_type}]")
        self.bundle_type = bundle_type
        self.selected_designer = selected_designer

        if self.bundle_type == "2+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~2번째 복장 상세 요구사항",
                placeholder="1, 2번째 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 3번째 복장 요구사항 (2+1 보너스)",
                placeholder="3번째(보너스) 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.fourth_style)

        elif self.bundle_type == "3+1 묶음":
            self.gfx_style = discord.ui.TextInput(
                label="📝 1~3번째 복장 상세 요구사항",
                placeholder="1, 2, 3번째 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)

            self.fourth_style = discord.ui.TextInput(
                label="🎁 4번째 복장 요구사항 (3+1 보너스)",
                placeholder="4번째(보너스) 의상에 대한 요구사항을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.fourth_style)

        else:
            self.gfx_style = discord.ui.TextInput(
                label="📝 원하는 스타일 및 설명",
                placeholder="원하시는 콘셉트, 색감, 디테일 등을 적어주세요.",
                required=True, 
                style=discord.TextStyle.paragraph, 
                max_length=1000
            )
            self.add_item(self.gfx_style)
            self.fourth_style = None

        self.roblox_nickname = None
        self.gfx_genre = None
