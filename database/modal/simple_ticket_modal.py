import re
import time
import discord
import aiosqlite
from datetime import datetime

from config import *
from database.database import DATABASE
from database.views.payment_view import PaymentView
from database.views.close_ticket import TicketCloseView
from database.views.claim_view import ClaimTicketView
from database.ticket_notice import build_ticket_notice_embed
from database.purchase_log import send_purchase_log
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    release_ticket_creation_lock,
)


class SimpleTicketModal(discord.ui.Modal):

    COMMISSION_NAME = "문의"
    MODAL_TITLE = "문의 신청서"
    FORM_TITLE = "📋 문의 신청서"
    FIELD_NAME = "내용"

    def __init__(self, ticket_type: str = "문의", selected_designer: int = None):
        self.COMMISSION_NAME = ticket_type
        self.MODAL_TITLE = f"{ticket_type} 신청서"
        self.FORM_TITLE = f"📋 {ticket_type} 접수"
        
        super().__init__(title=self.MODAL_TITLE)

        self.selected_designer = selected_designer

        # 파트너/제휴 신청인 경우 개편된 2개 입력란 구성 (서버 총 인원 / 서버 영구링크)
        if any(keyword in ticket_type for keyword in ["파트너", "제휴"]):
            self.member_count = discord.ui.TextInput(
                label="서버 총 인원 (파트너십 조건: 100명 이상)",
                placeholder="예: 150 (숫자만 입력)",
                style=discord.TextStyle.short,
                required=True,
                max_length=50
            )
            self.invite_link = discord.ui.TextInput(
                label="서버 영구링크",
                placeholder="https://discord.gg/... 형식의 영구 초대 링크",
                style=discord.TextStyle.short,
                required=True,
                max_length=200
            )
            self.add_item(self.member_count)
            self.add_item(self.invite_link)
        else:
            self.content = discord.ui.TextInput(
                label=self.FIELD_NAME,
                placeholder="원하시는 내용을 상세하게 작성해 주세요.",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000
            )
            self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        # 1. 3초 타임아웃 방지를 위한 defer 선제 처리
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 파트너 / 제휴 신청 시 서버 인원수(100명 이상) 검증
        if any(keyword in self.COMMISSION_NAME for keyword in ["파트너", "제휴"]):
            raw_count = self.member_count.value.strip().replace(",", "")
            if not raw_count.isdigit():
                return await interaction.followup.send("❌ 서버 총 인원란에는 숫자만 입력해 주세요.", ephemeral=True)

            members_count = int(raw_count)
            if members_count < 100:
                return await interaction.followup.send(
                    f"❌ 파트너십 신청은 **서버 인원 100명 이상** 조건일 때만 진행할 수 있습니다. (현재 입력값: {members_count:,}명)",
                    ephemeral=True
                )

        ticket_lock = await acquire_ticket_creation_lock(interaction)
        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)
        except discord.Forbidden:
            await interaction.followup.send("❌ 티켓을 생성할 권한이 없습니다. 봇 권한을 확인해주세요.", ephemeral=True)
        except Exception as error:
            print(f"[Simple ticket creation failed] {type(error).__name__}: {error}")
            await interaction.followup.send("❌ 문의 티켓을 생성하는 중 오류가 발생했습니다.", ephemeral=True)
        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # [중복 허용 관련 모듈] 더 이상 이전 티켓 체크로 막지 않음
        developer = guild.get_member(self.selected_designer) if self.selected_designer else None
        designer_name = developer.display_name if developer else "미지정"
        designer_mention = developer.mention if developer else "미지정"
        claim_view = ClaimTicketView(is_claimed=bool(developer))

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, view_channel=False),
            user: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, manage_channels=True)
        }

        if developer:
            overwrites[developer] = discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True)

        # --------------------------------------------------
        # 채널명 및 토픽 가독성 개선 (카테고리/디자이너 구별)
        # --------------------------------------------------
        # 영문/숫자 외 디스코드 채널명 호환을 위한 단순 정리
        safe_category = re.sub(r'[^a-zA-Z0-9가-힣]', '', self.COMMISSION_NAME).lower()
        safe_designer = re.sub(r'[^a-zA-Z0-9가-힣]', '', designer_name).lower()
        safe_user = re.sub(r'[^a-zA-Z0-9가-힣]', '', user.display_name).lower()
        time_suffix = str(int(time.time()))[-4:] # 중복 생성 시 채널명 충돌 방지용 고유 번호

        # 채널명 예시: 티켓-gfx-홍길동-손님닉네임-1234 또는 티켓-복장-미지정-손님닉네임-5678
        channel_name = f"티켓-{safe_category}-{safe_designer}-{safe_user}-{time_suffix}"

        # 채널 토픽에 카테고리, 디자이너, 손님 ID 명시
        channel_topic = f"손님 ID: {user.id} | 카테고리: {self.COMMISSION_NAME} | 담당 디자이너: {designer_name}"

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=channel_topic
        )

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                INSERT INTO commissions(
                    ticket_channel, customer_id, designer_id, category, status, progress, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_channel.id,
                    user.id,
                    self.selected_designer or 0,
                    self.COMMISSION_NAME,
                    "in_progress",
                    0,
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                )
            )
            await db.commit()

        embed = discord.Embed(title=self.FORM_TITLE, color=0x5865F2, timestamp=datetime.now())
        embed.add_field(name="🏷️ 신청 카테고리", value=f"`{self.COMMISSION_NAME}`", inline=True)
        embed.add_field(name="👨‍💻 담당 디자이너", value=designer_mention, inline=True)

        # 파트너/제휴 신청일 경우 출력할 임베드 항목 구분
        if any(keyword in self.COMMISSION_NAME for keyword in ["파트너", "제휴"]):
            raw_count = self.member_count.value.strip().replace(",", "")
            members_count = int(raw_count) if raw_count.isdigit() else 0
            embed.add_field(name="👥 서버 총 인원", value=f"`{members_count:,}명`", inline=False)
            embed.add_field(name="🔗 서버 영구링크", value=self.invite_link.value, inline=False)
        else:
            embed.add_field(name=self.FIELD_NAME, value=self.content.value, inline=False)

        await ticket_channel.send(
            content=f"{user.mention}\n신청이 접수되었습니다. (**{self.COMMISSION_NAME}** / 담당: {designer_mention})",
            embed=embed,
            view=claim_view
        )

        # 안내 임베드 발송
        try:
            guide_embed = build_ticket_notice_embed()
            ref_embed = discord.Embed(
                title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
                description=f"{user.mention}님, 원하시는 참고용 이미지/파일이 있다면 이 채널에 구체적으로 올려주세요!",
                color=0x5865F2
            )
            ref_embed.set_footer(text="참고 자료가 상세할수록 신속하고 명확한 안내가 가능합니다 ✨")
            await ticket_channel.send(embeds=[guide_embed, ref_embed])
        except Exception as notice_err:
            print(f"[안내 임베드 생성/전송 오류] {notice_err}")

        # 구매/신청 로그 처리
        try:
            log_channel_name = globals().get('LOG_CHANNEL_NAME', None)
            if log_channel_name and discord.utils.get(guild.text_channels, name=log_channel_name):
                await send_purchase_log(guild, content=(
                    f"📩 새로운 [{self.COMMISSION_NAME}] 티켓 생성\n"
                    f"담당: {designer_mention}\n"
                    f"채널: {ticket_channel.mention}\n"
                    f"신청자 : {user.mention}"
                ))
        except Exception as log_err:
            print(f"[로그 전송 실패] {log_err}")

        # 디자이너 컨트롤러 DM 발송
        if developer:
            try:
                await developer.send(f"🔔 새로운 [{self.COMMISSION_NAME}] 문의가 들어왔습니다.\n{ticket_channel.mention}")
                await developer.send(
                    f"💳 결제 및 티켓 관리\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                    view=PaymentView(ticket_channel, self.selected_designer)
                )
                await developer.send(
                    f"🔒 티켓 종료 / 🗑️ 티켓 삭제\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                    view=TicketCloseView(ticket_channel)
                )
            except Exception as e:
                print(f"[DM 전송 실패] designer_id={self.selected_designer} error={e}")
                await ticket_channel.send(
                    f"{developer.mention} DM 전송에 실패하여 티켓에 관리 버튼을 전송합니다.",
                    allowed_mentions=discord.AllowedMentions(users=True)
                )
                await ticket_channel.send("💳 결제 및 티켓 관리", view=PaymentView(ticket_channel, self.selected_designer))
                await ticket_channel.send("🔒 티켓 종료 / 🗑️ 티켓 삭제", view=TicketCloseView(ticket_channel))

        await interaction.followup.send(f"✅ 신청 완료!\n{ticket_channel.mention}", ephemeral=True)
