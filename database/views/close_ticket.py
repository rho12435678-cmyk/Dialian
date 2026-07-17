import discord
import asyncio
import re
import aiosqlite

from datetime import datetime
from config import *
from database.database import DATABASE


def has_designer_role(member):
    if member is None:
        return False

    role_ids = {
        role_id
        for role_id in DESIGNER_ROLE_IDS.values()
        if role_id
    }

    return any(role.id in role_ids for role in member.roles)


def sanitize_text(text):
    if not text:
        return "[내용 없음]"

    text = re.sub(r'https?://\S+', "[LINK]", text)
    text = re.sub(r"discord\.gg/\S+", "[INVITE]", text)
    text = re.sub(r"\S+@\S+", "[EMAIL]", text)
    text = re.sub(r"\d{2,3}-\d{3,4}-\d{4}", "[PHONE]", text)
    text = re.sub(r"\d{6,}", "[NUMBER]", text)

    return text[:80]


async def delete_ticket_dm_messages(bot_user, designer, ticket_channel):
    if designer is None:
        return 0

    deleted = 0

    try:
        dm_channel = designer.dm_channel or await designer.create_dm()

        async for msg in dm_channel.history(
            limit=100,
            after=ticket_channel.created_at
        ):
            if msg.author.id != bot_user.id:
                continue

            try:
                await msg.delete()
                deleted += 1
            except Exception as e:
                print(f"[DM 삭제 실패] message_id={msg.id} error={e}")

    except Exception as e:
        print(f"[DM 정리 실패] designer={designer} error={e}")

    return deleted


async def get_or_create_archive_category(guild):
    category = discord.utils.get(
        guild.categories,
        name=ARCHIVE_CATEGORY_NAME
    )

    if category:
        return category

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            manage_channels=True
        )
    }

    return await guild.create_category(
        ARCHIVE_CATEGORY_NAME,
        overwrites=overwrites,
        reason="티켓 아카이브 카테고리 자동 생성"
    )


async def archive_ticket_channel(channel):
    guild = channel.guild
    archive_category = await get_or_create_archive_category(guild)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True
        )
    }

    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    await channel.edit(
        name=f"보관-{channel.name}",
        category=archive_category,
        overwrites=overwrites,
        sync_permissions=False,
        reason="티켓 종료 후 아카이브"
    )


def is_ticket_or_archive_channel(channel):
    return (
        isinstance(channel, discord.TextChannel)
        and (
            channel.name.startswith("티켓-")
            or channel.name.startswith("보관-티켓-")
        )
    )


def parse_mention_id(text):
    match = re.search(r"<@!?(\d+)>", text or "")
    return int(match.group(1)) if match else None


async def find_ticket_designer_id(channel):
    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            for field in embed.fields:
                designer_id = parse_mention_id(field.value)

                if designer_id:
                    return designer_id

            designer_id = parse_mention_id(embed.description)

            if designer_id:
                return designer_id

    return None


def can_manage_ticket(member, user_id, designer_id):
    if member is None:
        return False

    if member.guild_permissions.administrator:
        return True

    if designer_id is not None:
        return user_id == designer_id

    return has_designer_role(member)


async def delete_ticket_channel(channel, deleted_by=None):
    reason = "티켓 삭제"

    if deleted_by:
        reason = f"티켓 삭제: {deleted_by} ({deleted_by.id})"

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """
            UPDATE commissions
            SET status = 'cancelled',
                updated_at = ?
            WHERE ticket_channel = ?
              AND status != 'completed'
            """,
            (datetime.now().isoformat(), channel.id)
        )
        await db.commit()

    await channel.delete(reason=reason)


class TicketCloseView(discord.ui.View):

    def __init__(self, ticket_channel):
        super().__init__(timeout=None)

        self.ticket_channel = ticket_channel
        
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

            channel = self.ticket_channel
            guild = channel.guild

            if not channel.name.startswith("티켓-"):
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

                    if "커미션 신청서" not in embed.title:
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

            closer = guild.get_member(interaction.user.id)
            is_manager = (
                closer is not None
                and closer.guild_permissions.administrator
            )
            is_assigned_designer = (
                designer_id is not None
                and interaction.user.id == designer_id
            )
            is_role_designer = (
                designer_id is None
                and has_designer_role(closer)
            )

            if not (is_manager or is_assigned_designer or is_role_designer):
                return await interaction.followup.send(
                    "❌ 담당 디자이너 또는 관리자만 티켓을 종료할 수 있습니다.",
                    ephemeral=True
                )

            dm_deleted_count = 0

            if designer_id:
                designer = guild.get_member(designer_id)

                if designer is None:
                    try:
                        designer = await guild.fetch_member(designer_id)
                    except Exception:
                        designer = None

                dm_deleted_count = await delete_ticket_dm_messages(
                    interaction.client.user,
                    designer,
                    channel
                )

            # 오래된 티켓(Topic 없는 티켓) 호환
            if ticket_owner is None:
                try:
                    async for msg in channel.history(
                        limit=1,
                        oldest_first=True
                    ):
                        if msg.mentions:
                            ticket_owner = msg.mentions[0]
                            break
                except Exception:
                    pass

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
    value=ticket_owner.mention if ticket_owner else "알 수 없음",
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
                    text="개인정보는 저장되지 않았습니다."
                )

                await log_channel.send(
                    content=(
                        f"🔒 {ticket_owner.mention} 님의 티켓이 종료되었습니다."
                        if ticket_owner
                        else "🔒 티켓이 종료되었습니다."
                    ),
                    embed=safe_log_embed
                )

            archive_notice = discord.Embed(
                title="🔒 티켓이 종료되었습니다",
                description=(
                    "상담 기록 보관을 위해 이 채널은 잠시 후 "
                    "아카이브로 이동합니다."
                ),
                color=discord.Color.dark_grey(),
                timestamp=datetime.now()
            )

            archive_notice.add_field(
                name="처리 내용",
                value=(
                    "• 구매/상담 로그 저장\n"
                    "• 디자이너 관리 DM 정리\n"
                    "• 채널 보관함 이동"
                ),
                inline=False
            )

            archive_notice.set_footer(
                text="이 채널은 삭제되지 않고 관리자 전용 보관함에 저장됩니다."
            )

            await channel.send(embed=archive_notice)
            await asyncio.sleep(5)
            await archive_ticket_channel(channel)

        except Exception as e:
            print(f"[티켓 닫기 에러] {e}")

    @discord.ui.button(
        label="🗑️ 티켓 삭제",
        style=discord.ButtonStyle.secondary,
        custom_id="delete_ticket"
    )
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        try:
            channel = self.ticket_channel
            guild = channel.guild

            if not is_ticket_or_archive_channel(channel):
                return await interaction.response.send_message(
                    "❌ 올바른 티켓 채널이 아닙니다.",
                    ephemeral=True
                )

            designer_id = await find_ticket_designer_id(channel)
            deleter = guild.get_member(interaction.user.id)

            if not can_manage_ticket(deleter, interaction.user.id, designer_id):
                return await interaction.response.send_message(
                    "❌ 담당 디자이너 또는 관리자만 티켓을 삭제할 수 있습니다.",
                    ephemeral=True
                )

            await interaction.response.send_message(
                "🗑️ 티켓을 삭제합니다.",
                ephemeral=True
            )
            await asyncio.sleep(3)
            await delete_ticket_channel(channel, interaction.user)

        except Exception as e:
            print(f"[티켓 삭제 에러] {e}")
