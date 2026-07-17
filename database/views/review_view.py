import discord
import aiosqlite
import re

from datetime import datetime
from config import *

DATABASE = "data/dialian.db"


def parse_designer_id(text):
    match = re.search(r"<@!?(\d+)>", text or "")

    if match:
        return int(match.group(1))

    return None


async def find_designer_id_from_ticket(channel):
    if not isinstance(channel, discord.TextChannel):
        return None

    async for msg in channel.history(limit=50, oldest_first=True):
        for embed in msg.embeds:
            for field in embed.fields:
                designer_id = parse_designer_id(field.value)

                if designer_id:
                    return designer_id

            designer_id = parse_designer_id(embed.description)

            if designer_id:
                return designer_id

    return None


class StarRatingView(discord.ui.View):

    def __init__(self, designer_id=None):
        super().__init__(timeout=None)
        self.designer_id = designer_id

    @discord.ui.button(
        label="⭐ 1점",
        style=discord.ButtonStyle.secondary,
        custom_id="star_1"
    )
    async def star_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 1)

    @discord.ui.button(
        label="⭐ 2점",
        style=discord.ButtonStyle.secondary,
        custom_id="star_2"
    )
    async def star_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 2)

    @discord.ui.button(
        label="⭐ 3점",
        style=discord.ButtonStyle.secondary,
        custom_id="star_3"
    )
    async def star_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 3)

    @discord.ui.button(
        label="⭐ 4점",
        style=discord.ButtonStyle.secondary,
        custom_id="star_4"
    )
    async def star_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 4)

    @discord.ui.button(
        label="⭐ 5점",
        style=discord.ButtonStyle.success,
        custom_id="star_5"
    )
    async def star_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_rating(interaction, 5)

    async def process_rating(
        self,
        interaction: discord.Interaction,
        stars: int
    ):

        try:

            await interaction.response.defer(ephemeral=True)

            ticket_owner = interaction.user
            channel = interaction.channel

            if isinstance(channel, discord.TextChannel) and channel.topic:
                try:
                    owner_id = int(channel.topic)
                except ValueError:
                    owner_id = None

                if owner_id and interaction.user.id != owner_id:
                    return await interaction.followup.send(
                        "❌ 티켓을 생성한 구매자만 후기를 남길 수 있습니다.",
                        ephemeral=True
                    )

            guild = None

            for g in interaction.client.guilds:

                if discord.utils.get(
                    g.text_channels,
                    name=REVIEW_CHANNEL_NAME
                ):
                    guild = g
                    break

            if guild is None:

                guild = (
                    interaction.client.guilds[0]
                    if interaction.client.guilds
                    else None
                )

            if guild is None:
                return await interaction.followup.send(
                    "연동된 서버를 찾을 수 없습니다.",
                    ephemeral=True
                )

            review_channel = discord.utils.get(
                guild.text_channels,
                name=REVIEW_CHANNEL_NAME
            )

            if review_channel is None:

                review_channel = next(
                    (
                        ch
                        for ch in guild.text_channels
                        if "후기" in ch.name
                    ),
                    None
                )


            if review_channel:

                star_emojis = "⭐" * stars

                review_embed = discord.Embed(
                    title="✨ 소중한 커미션 후기가 도착했습니다!",
                    color=0xFEE75C,
                    timestamp=datetime.now()
                )

                review_embed.set_thumbnail(
                    url=ticket_owner.display_avatar.url
                )

                review_embed.add_field(
                    name="👤 작성자(오너)",
                    value=f"{ticket_owner.mention} ({ticket_owner.name})",
                    inline=True
                )

                review_embed.add_field(
                    name="📊 만족도 별점",
                    value=f"**{star_emojis} ({stars} / 5점)**",
                    inline=True
                )

                review_embed.set_footer(
                    text="만족스러운 서비스를 제공하기 위해 항상 노력하겠습니다 🙏"
                )

                sent_review = await review_channel.send(
                    embed=review_embed
                )

                designer_id = (
                    self.designer_id
                    or await find_designer_id_from_ticket(channel)
                )
                print(f"[REVIEW] designer_id = {designer_id}")

                # ==========================
                # SQLite 저장
                # ==========================

                if designer_id:

                    async with aiosqlite.connect(DATABASE) as db:

                        await db.execute(
                            """
                            INSERT INTO reviews(
                                developer_id,
                                customer_id,
                                stars,
                                review,
                                created_at
                            )
                            VALUES(?,?,?,?,?)
                            """,
                            (
                                designer_id,
                                interaction.user.id,
                                stars,
                                "",
                                datetime.now().isoformat()
                            )
                        )

                        await db.commit()
                        print("[REVIEW] DB 저장 완료")

                role_notice = ""

                try:
                    buyer_role = guild.get_role(BUYER_ROLE_ID)
                    buyer_member = guild.get_member(interaction.user.id)

                    if (
                        buyer_member
                        and buyer_role
                        and buyer_role not in buyer_member.roles
                    ):
                        await buyer_member.add_roles(
                            buyer_role,
                            reason="별점 후기 제출 후 구매자 역할 지급"
                        )
                        role_notice = "\n구매자 역할이 지급되었습니다."

                except Exception as role_err:
                    print(f"[구매자 역할 지급 실패] {role_err}")

                success_view = discord.ui.View()

                success_view.add_item(
                    discord.ui.Button(
                        label="😄 내가 쓴 후기 보러가기",
                        url=sent_review.jump_url,
                        style=discord.ButtonStyle.link
                    )
                )

                await interaction.followup.send(
                    f"🎉 성공적으로 **{stars}점** 별점이 제출되었습니다!{role_notice}",
                    view=success_view,
                    ephemeral=True
                )

                disabled_view = discord.ui.View()

                for i in range(1, 6):

                    style = (
                        discord.ButtonStyle.success
                        if i == 5
                        else discord.ButtonStyle.secondary
                    )

                    disabled_view.add_item(
                        discord.ui.Button(
                            label=f"⭐ {i}점",
                            style=style,
                            custom_id=f"star_{i}",
                            disabled=True
                        )
                    )

                await interaction.message.edit(
                    view=disabled_view
                )

            else:

                await interaction.followup.send(
                    "후기 채널을 찾을 수 없습니다.",
                    ephemeral=True
                )

        except Exception as e:
            print(f"[별점 등록 에러] {e}")
