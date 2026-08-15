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
        # 1. 디스코드 3초 타임아웃 방지를 위해 지연 응답 처리
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        ticket_lock = await acquire_ticket_creation_lock(interaction)
        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)
        except discord.Forbidden:
            await interaction.followup.send("❌ 티켓을 생성할 권한이 없습니다. 봇 권한을 확인해 주세요.", ephemeral=True)
        except Exception as error:
            print(f"[Simple ticket creation failed] {type(error).__name__}: {error}")
            await interaction.followup.send("❌ 문의 티켓을 생성하는 중 오류가 발생했습니다.", ephemeral=True)
        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # 이미 열린 티켓이 있는지 검사
        if get_open_ticket_channel(guild, user):
            return await interaction.followup.send("❌ 이미 생성된 티켓이 있습니다.", ephemeral=True)

        developer = guild.get_member(self.selected_designer) if self.selected_designer else None
        designer_name = developer.mention if developer else "미지정"
        claim_view = ClaimTicketView(is_claimed=bool(developer))

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, view_channel=False),
            user: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, manage_channels=True)
        }

        if developer:
            overwrites[developer] = discord.PermissionOverwrite(read_messages=True, view_channel=True, send_messages=True, attach_files=True)

        ticket_channel = await guild.create_text_channel(
            name=f"티켓-{user.id}",
            overwrites=overwrites,
            topic=str(user.id)
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
        embed.add_field(name="👨‍💻 담당 디자이너", value=designer_name, inline=False)
        embed.add_field(name=self.FIELD_NAME, value=self.content.value, inline=False)

        await ticket_channel.send(
            content=f"{user.mention}\n신청이 접수되었습니다. 담당자가 확인 후 안내드릴 예정입니다.",
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
                    f"📩 새로운 {self.COMMISSION_NAME} 티켓 생성\n"
                    f"{ticket_channel.mention}\n"
                    f"신청자 : {user.mention}"
                ))
        except Exception as log_err:
            print(f"[로그 전송 실패] {log_err}")

        # 디자이너 컨트롤러 DM 처리
        if developer:
            try:
                await developer.send(f"🔔 새로운 문의가 들어왔습니다.\n{ticket_channel.mention}")
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
                print(f"[DM 전송 실패 -> 채널 백업 전송] developer={developer} error={e}")
                await ticket_channel.send(
                    f"{developer.mention} DM 전송 실패로 인해 티켓 채널에 관리용 버튼을 생성합니다.",
                    allowed_mentions=discord.AllowedMentions(users=True)
                )
                await ticket_channel.send("💳 결제 및 티켓 관리", view=PaymentView(ticket_channel, self.selected_designer))
                await ticket_channel.send("🔒 티켓 종료 / 🗑️ 티켓 삭제", view=TicketCloseView(ticket_channel))

        await interaction.followup.send(f"✅ 신청 완료!\n{ticket_channel.mention}", ephemeral=True)
