import discord
import aiosqlite
from datetime import datetime

from database.database import DATABASE
from database.views.ticket_guard import (
    acquire_ticket_creation_lock,
    get_open_ticket_channel,
    release_ticket_creation_lock,
)
from database.purchase_log import send_purchase_log


# 개발자 지원 티켓을 확인할 관리자 계정
ADMIN_IDS = [
    727462527235260427,
    1468584582113919129,
    859756809865789451,
    513508955264385032,
    1048172184369119302,
    375938495350571009,
]


# --------------------------------------------------
# 🛠️ 개발자 지원 심사 전용 컨트롤 버튼 View
# --------------------------------------------------
class DeveloperReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ 합격",
        style=discord.ButtonStyle.success,
        custom_id="dev_pass_btn"
    )
    async def pass_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_IDS and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 관리자만 심사할 수 있습니다.", ephemeral=True)

        embed = discord.Embed(
            title="🎉 지원 결과: 최종 합격",
            description=f"축하합니다! **{interaction.user.mention}** 관리자에 의해 개발자 지원이 **최종 합격** 처리되었습니다.",
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 합격 처리가 완료되었습니다.", ephemeral=True)

    @discord.ui.button(
        label="💬 면접 안내",
        style=discord.ButtonStyle.primary,
        custom_id="dev_interview_btn"
    )
    async def interview_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_IDS and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 관리자만 심사할 수 있습니다.", ephemeral=True)

        embed = discord.Embed(
            title="💬 지원 결과: 면접 대상자 선정",
            description="서류 심사에 통과하셨습니다. 관리자가 이 티켓에서 추가 면접 일정을 안내해드릴 예정입니다.",
            color=discord.Color.blue()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 면접 안내 메시지를 전송했습니다.", ephemeral=True)

    @discord.ui.button(
        label="❌ 불합격",
        style=discord.ButtonStyle.danger,
        custom_id="dev_fail_btn"
    )
    async def fail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_IDS and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 관리자만 심사할 수 있습니다.", ephemeral=True)

        embed = discord.Embed(
            title="📢 지원 결과 안내",
            description="아쉽게도 이번 개발자 모집에서는 함께하지 못하게 되었습니다. 지원해주셔서 감사합니다.",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 불합격 처리가 완료되었습니다.", ephemeral=True)


# --------------------------------------------------
# 📝 개발자 지원 신청서 모달
# --------------------------------------------------
class DeveloperApplyModal(discord.ui.Modal, title="개발자 지원"):

    field = discord.ui.TextInput(
        label="지원 분야",
        placeholder="예: GFX / Roblox 복장",
        required=True,
        max_length=30
    )

    experience = discord.ui.TextInput(
        label="경력",
        placeholder="예: 2년 / 없음",
        required=True,
        max_length=100
    )

    program = discord.ui.TextInput(
        label="사용 가능 프로그램",
        placeholder="예: Blender, Photoshop",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        ticket_lock = await acquire_ticket_creation_lock(interaction)

        if ticket_lock is None:
            return

        try:
            await self.create_ticket(interaction)

        except discord.Forbidden:
            msg = "❌ 봇에 채널 생성 또는 권한 설정 권한이 없습니다."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

        except Exception as error:
            print(f"[개발자 지원 티켓 생성 오류] {error}")
            msg = "❌ 지원 티켓을 생성하는 중 오류가 발생했습니다."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

        finally:
            release_ticket_creation_lock(ticket_lock)

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            return await interaction.response.send_message(
                "❌ 서버 안에서만 사용할 수 있습니다.",
                ephemeral=True
            )

        existing_channel = get_open_ticket_channel(guild, user)

        if existing_channel:
            return await interaction.response.send_message(
                f"이미 생성된 티켓이 있습니다.\n{existing_channel.mention}",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        bot_member = guild.me
        if bot_member is None and interaction.client.user:
            bot_member = guild.get_member(interaction.client.user.id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            )
        }

        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True
            )

        for admin_id in ADMIN_IDS:
            admin = guild.get_member(admin_id)
            if admin:
                overwrites[admin] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True
                )

        ticket_channel = await guild.create_text_channel(
            name=f"개발자지원-{user.id}",
            overwrites=overwrites,
            topic=str(user.id),
            reason=f"{user}의 개발자 지원 티켓"
        )

        now = datetime.now().isoformat()

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
                    None,
                    "개발자 지원",
                    "in_progress",
                    0,
                    now,
                    now
                )
            )
            await db.commit()

        embed = discord.Embed(
            title="🛠️ 개발자 지원서",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="지원자", value=f"{user.mention}\n`{user.id}`", inline=False)
        embed.add_field(name="지원 분야", value=self.field.value, inline=False)
        embed.add_field(name="경력", value=self.experience.value, inline=False)
        embed.add_field(name="사용 가능 프로그램", value=self.program.value, inline=False)
        embed.set_footer(text=f"지원자: {user}")

        # 임베드 전송 시 심사 전용 버튼 View(DeveloperReviewView) 부착
        await ticket_channel.send(
            content=(
                f"{user.mention}\n"
                "✅ 개발자 지원서가 접수되었습니다. "
                "관리자가 확인 후 아래 심사 버튼 또는 메시지로 안내해 드리겠습니다."
            ),
            embed=embed,
            view=DeveloperReviewView(),
            allowed_mentions=discord.AllowedMentions(users=True)
        )

        await ticket_channel.send(
            "📎 포트폴리오, 작업물, 증명 자료는 이 티켓에 첨부파일로 자유롭게 올려주세요."
        )

        await send_purchase_log(
            guild,
            content=f"개발자 지원 티켓 생성\n{ticket_channel.mention}\n신청자: {user.mention}",
        )

        await interaction.followup.send(
            f"✅ 지원서가 제출되었습니다.\n{ticket_channel.mention}",
            ephemeral=True
        )
