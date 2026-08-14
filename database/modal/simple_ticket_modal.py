import discord
import aiosqlite
from datetime import datetime

from config import *
from database.database import DATABASE
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView
from database.views.close_ticket import TicketCloseView
from database.views.claim_view import ClaimTicketView
from database.ticket_notice import build_ticket_notice_embed
from database.purchase_log import send_purchase_log
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)


class SimpleTicketModal(discord.ui.Modal):

    COMMISSION_NAME = "문의"
    MODAL_TITLE = "문의 신청서"
    FORM_TITLE = "📋 문의 신청서"
    FIELD_NAME = "내용"

    def __init__(self, ticket_type: str = "문의", selected_designer: int = None):
        # 파트너 문의, 기타 문의 등 유형에 맞춰 제목 및 명칭 변경
        self.COMMISSION_NAME = ticket_type
        self.MODAL_TITLE = f"{ticket_type} 신청서"
        self.FORM_TITLE = f"📋 {ticket_type} 접수"
        
        super().__init__(title=self.MODAL_TITLE)

        self.selected_designer = selected_designer

        self.content = discord.ui.TextInput(
            label=self.FIELD_NAME,
            placeholder="원하시는 내용을 상세하게 작성해 주세요.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        ticket_lock = await acquire_ticket_creation_lock(interaction)

        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)
        except discord.Forbidden:
            message = "티켓을 생성할 권한이 없습니다. 봇 권한을 확인해주세요."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as error:
            print(f"[Simple ticket creation failed] {type(error).__name__}: {error}")
            message = "문의 티켓을 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(self, interaction: discord.Interaction):

        guild = interaction.guild
        user = interaction.user

        # 디자이너 배정 여부에 따른 View 생성
        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)
            designer_name = developer.mention if developer else "미지정"
            claim_view = ClaimTicketView(is_claimed=True)   # 배정 완료 시 버튼 비활성화
        else:
            designer_name = "미지정"
            claim_view = ClaimTicketView(is_claimed=False)  # 미배정 시 [내가 담당하기] 버튼 활성화

        ticket_channel_name = f"티켓-{user.id}"

        if get_open_ticket_channel(guild, user):
            return await interaction.response.send_message(
                "이미 생성된 티켓이 있습니다.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        # 보안 설정: 일반 유저 채널 열람 권한 원천 차단
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False,
                view_channel=False
            ),
            user: discord.PermissionOverwrite(
                read_messages=True,
                view_channel=True,
                send_messages=True,
                attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                view_channel=True,
                send_messages=True
            )
        }

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                overwrites[developer] = discord.PermissionOverwrite(
                    read_messages=True,
                    view_channel=True,
                    send_messages=True,
                    attach_files=True
                )

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            overwrites=overwrites,
            topic=str(user.id)
        )

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
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_channel.id,
                    user.id,
                    self.selected_designer if self.selected_designer else 0,
                    self.COMMISSION_NAME,
                    "in_progress",
                    0,
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                )
            )
            await db.commit()

        embed = discord.Embed(
            title=self.FORM_TITLE,
            color=0x5865F2,
            timestamp=datetime.now()
        )

        embed.add_field(
            name="👨‍💻 담당 디자이너",
            value=designer_name,
            inline=False
        )

        embed.add_field(
            name=self.FIELD_NAME,
            value=self.content.value,
            inline=False
        )

        # 1. 신청서 및 멘션 전송
        await ticket_channel.send(
            content=(
                f"{user.mention}\n"
                "신청이 접수되었습니다. 담당자가 확인 후 안내드릴 예정입니다."
            ),
            embed=embed
        )

        # 2. 안내 및 참고자료 임베드 묶음 전송
        try:
            guide_embed = build_ticket_notice_embed()
            ref_embed = discord.Embed(
                title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
                description=(
                    f"{user.mention}님, 원하시는 구체적인 구도, 분위기, 색감, "
                    "또는 참고용 이미지/파일이 있다면 이 채널에 구체적으로 올려주세요!"
                ),
                color=0x5865F2
            )
            ref_embed.set_footer(text="참고 자료가 상세할수록 신속하고 명확한 안내가 가능합니다 ✨")
            await ticket_channel.send(embeds=[guide_embed, ref_embed])
        except Exception as notice_err:
            print(f"[안내 임베드 생성/전송 오류] {notice_err}")

        # 3. 진행 임베드 전송 ([내가 담당하기] View 부착)
        progress_embed = discord.Embed(
            title="📌 커미션/문의 진행",
            description=(
                f"👨‍💻 담당 디자이너 : {designer_name}\n\n"
                "📌 상태 : 🟢 상담중\n"
                "📊 진행률 : 0%\n"
                "⏰ 예상 완료 : 미설정"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now()
        )

        progress_message = await ticket_channel.send(embed=progress_embed, view=claim_view)

        # 4. 구매/신청 로그 처리
        try:
            log_channel_name = globals().get('LOG_CHANNEL_NAME', None)
            if log_channel_name:
                log_channel = discord.utils.get(guild.text_channels, name=log_channel_name)
                if log_channel:
                    await send_purchase_log(guild, content=(
                        f"📩 새로운 {self.COMMISSION_NAME} 티켓 생성\n"
                        f"{ticket_channel.mention}\n"
                        f"신청자 : {user.mention}"
                    ))
        except Exception as log_err:
            print(f"[로그 전송 실패] {log_err}")

        # 5. 지정 디자이너 DM 및 컨트롤러 제어
        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                try:
                    await developer.send(
                        f"🔔 새로운 문의가 들어왔습니다.\n"
                        f"{ticket_channel.mention}"
                    )

                    await developer.send(
                        f"📊 진행률 관리\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                        view=ProgressView(progress_message, self.selected_designer)
                    )

                    await developer.send(
                        f"💳 결제 및 티켓 관리\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                        view=PaymentView(ticket_channel, self.selected_designer)
                    )

                    await developer.send(
                        f"🔒 티켓 종료 / 🗑️ 티켓 삭제\n티켓: {ticket_channel.mention}\nID: {ticket_channel.id}",
                        view=TicketCloseView(ticket_channel)
                    )

                except Exception as e:
                    print(
                        f"[DM 전송 실패] designer_id={self.selected_designer} "
                        f"user={developer} error={e}"
                    )
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
                print(
                    f"[DM 전송 실패] 서버에서 디자이너를 찾지 못했습니다: "
                    f"{self.selected_designer}"
                )

        await interaction.followup.send(
            f"✅ 신청 완료!\n{ticket_channel.mention}",
            ephemeral=True
        )
