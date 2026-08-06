import discord
import aiosqlite

from datetime import datetime
from config import *
from database.database import DATABASE
from database.views.progress_view import ProgressView
from database.views.payment_view import PaymentView
from database.views.close_ticket import TicketCloseView
from database.ticket_notice import build_ticket_notice_embed
from database.purchase_log import send_purchase_log
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)


class SimpleTicketModal(discord.ui.Modal):

    COMMISSION_NAME = "로고"
    MODAL_TITLE = "커미션 신청서"
    FORM_TITLE = "📋 커미션 신청서"
    FIELD_NAME = "내용"

    def __init__(self):
        super().__init__(title=self.MODAL_TITLE)

        self.selected_designer = None

        self.content = discord.ui.TextInput(
            label=self.FIELD_NAME,
            placeholder="원하는 내용을 자유롭게 작성해주세요.",
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

        designer_name = "미지정"

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)
            designer_name = developer.mention if developer else "미지정"

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
                    self.selected_designer,
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

        # 신청서 및 멘션 전송
        await ticket_channel.send(
            content=(
                f"{user.mention}\n"
                "신청이 접수되었습니다. 담당 디자이너가 확인 후 안내드릴 예정입니다."
            ),
            embed=embed
        )

        # 안내/참고자료/진행 임베드 3종 동시 전송 구성
        guide_embed = build_ticket_notice_embed()
        
        ref_embed = discord.Embed(
            title="🖼️ 참고 자료(이미지/파일) 첨부 안내",
            description=(
                f"{user.mention}님, 디자이너가 원하시는 스타일을 명확히 파악할 수 있도록\n"
                "**원하시는 구도, 분위기, 색감, 참고용 이미지/파일**을 이 채널에 구체적으로 올려주세요!"
            ),
            color=0x5865F2
        )
        ref_embed.set_footer(text="참고 자료가 상세할수록 높은 완성도의 결과물이 나옵니다 ✨")

        progress_embed = discord.Embed(
            title="📌 커미션 진행",
            description=(
                f"👨‍💻 담당 디자이너 : {designer_name}\n\n"
                "📌 상태 : 🟢 상담중\n"
                "📊 진행률 : 0%\n"
                "⏰ 예상 완료 : 미설정"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now()
        )

        # 세 개의 임베드를 한 번에 전송
        await ticket_channel.send(embeds=[guide_embed, ref_embed, progress_embed])

        # progress_message 변수 지정을 위한 참조용 진행 메시지 저장
        progress_message = await ticket_channel.send(embed=progress_embed)

        log_channel = discord.utils.get(
            guild.text_channels,
            name=LOG_CHANNEL_NAME
        )

        if log_channel:
            await send_purchase_log(guild, content=(
                f"📩 새로운 로고 티켓 생성\n"
                f"{ticket_channel.mention}\n"
                f"신청자 : {user.mention}"
            ))

        if self.selected_designer:
            developer = guild.get_member(self.selected_designer)

            if developer:
                try:
                    await developer.send(
                        f"🔔 새로운 커미션이 들어왔습니다.\n"
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
