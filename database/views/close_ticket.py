import discord
import asyncio
import re

from datetime import datetime
from config import *
from database.views.review_view import StarRatingView
from database.views.payment_view import PaymentView
from database.views.progress_view import ProgressView


def sanitize_text(text):
    if not text:
        return "[내용 없음]"

    text = re.sub(r'https?://\S+', "[LINK]", text)
    text = re.sub(r"discord\.gg/\S+", "[INVITE]", text)
    text = re.sub(r"\S+@\S+", "[EMAIL]", text)
    text = re.sub(r"\d{2,3}-\d{3,4}-\d{4}", "[PHONE]", text)
    text = re.sub(r"\d{6,}", "[NUMBER]", text)

    return text[:80]


class TicketCloseView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 티켓 닫기",
        style=discord.ButtonStyle.danger,
        custom_id="close_ticket"
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        try:

    await interaction.response.defer()

    developer_ids = []

    for value in DESIGNERS.values():
        if isinstance(value, dict):
            if "id" in value:
                developer_ids.append(value["id"])
            else:
                developer_ids.extend(value.keys())

    if interaction.user.id not in developer_ids:
        return await interaction.followup.send(
            "❌ 관리자만 티켓을 종료할 수 있습니다.",
            ephemeral=True
        )

    channel = interaction.channel
    guild = interaction.guild

    if "티켓" not in channel.name:
        return await interaction.followup.send(
            "❌ 올바른 티켓 채널이 아닙니다.",
            ephemeral=True
        )

    ticket_owner = None
designer_id = None

# 채널 Topic에 저장된 구매자 ID 읽기
try:
    if channel.topic:
        ticket_owner = guild.get_member(int(channel.topic))
except Exception:
    pass

try:

    async for msg in channel.history(
        limit=20,
        oldest_first=True
    ):

        if not msg.embeds:
            continue

        embed = msg.embeds[0]

        if embed.title != "📋 커미션 신청서":
            continue

        for field in embed.fields:

            if field.name == "👨‍💻 담당 디자이너":

                if "<@" in field.value:

                    designer_id = int(
                        field.value.replace("<@", "")
                                   .replace("!", "")
                                   .replace(">", "")
                    )

                break

        if designer_id:
            break

except Exception:
    pass

# 오래된 티켓(Topic 없는 티켓) 호환
if ticket_owner is None:
    try:
        async for msg in channel.history(limit=1, oldest_first=True):
            if msg.mentions:
                ticket_owner = msg.mentions[0]
                break
    except Exception:
        pass

            await interaction.followup.send(
                "💾 안전하게 구매로그를 정리하는 중입니다..."
            )

            message_count = 0
            attachment_count = 0
            participants = set()
            recent_messages = []

            async for msg in channel.history(
                limit=100,
                oldest_first=False
            ):

                if msg.author.bot:
                    continue

                message_count += 1
                participants.add(msg.author.display_name)

                if msg.attachments:
                    attachment_count += len(msg.attachments)

                if len(recent_messages) < 3:

                    clean_content = sanitize_text(msg.content)

                    if not clean_content.strip():
                        clean_content = "[파일 또는 이미지]"

                    recent_messages.append(
                        f"• {msg.author.display_name}: {clean_content}"
                    )

            created_at = channel.created_at
            closed_at = datetime.now(created_at.tzinfo)

            duration = closed_at - created_at

            total_minutes = int(duration.total_seconds() // 60)
            hours = total_minutes // 60
            minutes = total_minutes % 60

            log_channel = discord.utils.get(
                guild.text_channels,
                name=LOG_CHANNEL_NAME
            )

            if log_channel:

                safe_log_embed = discord.Embed(
                    title="🧾 구매 / 상담 로그",
                    color=0x5865F2,
                    timestamp=datetime.now()
                )

                safe_log_embed.add_field(
                    name="👤 고객",
                    value=ticket_owner.mention,
                    inline=True
                )

                safe_log_embed.add_field(
                    name="🔒 종료자",
                    value=interaction.user.mention,
                    inline=True
                )

                safe_log_embed.add_field(
                    name="💬 메시지 수",
                    value=str(message_count),
                    inline=True
                )

                safe_log_embed.add_field(
                    name="⏱ 상담 시간",
                    value=f"{hours}시간 {minutes}분",
                    inline=True
                )

                safe_log_embed.add_field(
                    name="📎 첨부파일",
                    value=f"{attachment_count}개",
                    inline=True
                )

                safe_log_embed.add_field(
                    name="👥 참여자",
                    value=", ".join(sorted(participants)),
                    inline=False
                )

                safe_log_embed.set_footer(
                    text="전체 대화 내용 및 개인정보는 저장되지 않았습니다."
                )

                await log_channel.send(
                    content=f"🔒 {ticket_owner.mention} 님의 티켓이 종료되었습니다.",
                    embed=safe_log_embed
                )


            # ==============================
            # 구매자 역할 자동 지급
            # ==============================

            try:

                buyer_role = guild.get_role(BUYER_ROLE_ID)

                if (
                    buyer_role
                    and ticket_owner
                    and buyer_role not in ticket_owner.roles
                ):

                    await ticket_owner.add_roles(
                        buyer_role,
                        reason="커미션 완료 자동 구매자 역할 지급"
                    )

                    try:

                        success_role_embed = discord.Embed(
                            title="🎉 구매자 역할 지급 완료",
                            description=(
                                f"`{guild.name}` 서버에서\n"
                                "구매자 역할이 지급되었습니다!"
                            ),
                            color=discord.Color.green()
                        )

                        await ticket_owner.send(
                            embed=success_role_embed
                        )

                    except Exception:
                        pass

            except Exception as role_err:
                print(f"[구매자 역할 지급 실패] {role_err}")

            # ==============================
            # 유저 DM
            # ==============================

            try:

                dm_embed = discord.Embed(
                    title="💌 서비스를 이용해 주셔서 감사합니다!",
                    description=(
                        "진행하시던 커미션이 완료되어 티켓이 종료되었습니다.\n"
                        "아래 버튼을 통해 만족도 별점을 남겨주세요!"
                    ),
                    color=0x5865F2
                )

                await ticket_owner.send(
                    embed=dm_embed,
                    view=StarRatingView(designer_id)
                )

            except Exception as dm_e:
                print(f"[DM 실패] {dm_e}")


            await interaction.followup.send(
                "⚠️ 로그 정리 완료! 채널은 5초 후 삭제됩니다."
            )

            await asyncio.sleep(5)
            await channel.delete()

        except Exception as e:
            print(f"[티켓 닫기 에러] {e}")
