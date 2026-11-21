"""Persistent ticket review entry, private star picker, then a public-review modal."""
import discord

from ddh.reviews import eligible_review, review_channel, submit_review
from ddh.server import UserError, require_guild
from ddh.views import GuardedView, reply, report_error


async def open_review(bot, interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ticket = await eligible_review(bot, interaction.guild, interaction.channel, interaction.user)
    await review_channel(bot, interaction.guild)
    existing = await bot.store.commission_review(interaction.guild.id, ticket['id'])
    if existing and existing['rating'] is not None:
        await reply(interaction, '이미 이 의뢰의 후기를 제출했습니다. 게시가 지연된 후기는 자동 재시도합니다.')
        return
    await interaction.followup.send('별점을 선택한 다음 공개할 후기를 작성해 주세요. 의뢰당 한 번 제출할 수 있습니다.',
        view=ReviewRatingView(bot, interaction.user.id, interaction.channel_id, ticket['assigned_id']),
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ReviewRequestView(GuardedView):
    @discord.ui.button(label='⭐ 별점 후기 남기기', custom_id='ddh:commission:review:v1', style=discord.ButtonStyle.primary)
    async def review(self, interaction, button):
        await open_review(self.bot, interaction)


class ReviewReceiptView(GuardedView):
    def __init__(self, bot, *, url=None, closed=False):
        super().__init__(bot)
        self.close_ticket.disabled = closed
        if url:
            self.add_item(discord.ui.Button(label='후기 원문 보기', url=url))

    @discord.ui.button(label='의뢰 종료', custom_id='ddh:review:close:v1', style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction, button):
        from ddh.commission_views import open_commission_close
        await open_commission_close(self.bot, interaction)


class ReviewRatingView(GuardedView):
    def __init__(self, bot, owner_id, channel_id, developer_id):
        super().__init__(bot, timeout=300)
        self.owner_id, self.channel_id, self.developer_id = owner_id, channel_id, developer_id

    @discord.ui.select(placeholder='별점 선택 (1~5점)', options=[
        discord.SelectOption(label=f'{rating}점', value=str(rating), emoji='⭐') for rating in range(1, 6)])
    async def stars(self, interaction, select):
        if interaction.user.id != self.owner_id or interaction.channel_id != self.channel_id:
            raise UserError('본인의 의뢰 티켓에서 작성해 주세요.')
        await interaction.response.send_modal(ReviewModal(self.bot, self.owner_id, self.channel_id,
                                                         self.developer_id, int(select.values[0])))


class ReviewModal(discord.ui.Modal):
    def __init__(self, bot, owner_id, channel_id, developer_id, rating):
        super().__init__(title=f'공개 의뢰 후기 · {rating}/5점', timeout=600)
        self.bot, self.owner_id, self.channel_id = bot, owner_id, channel_id
        self.developer_id, self.rating = developer_id, rating
        self.body = discord.ui.TextInput(label='후기 채널에 공개할 내용 (선택)', required=False,
            max_length=1000, style=discord.TextStyle.paragraph,
            placeholder='작업·소통에 대한 경험을 적어 주세요. 계좌·연락처·비공개 자료는 적지 마세요.')
        self.add_item(self.body)

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        if interaction.user.id != self.owner_id or interaction.channel_id != self.channel_id:
            raise UserError('본인의 의뢰 티켓에서 작성해 주세요.')
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await submit_review(self.bot, interaction.guild, interaction.channel, interaction.user,
                                  self.rating, self.body.value, developer_id=self.developer_id)
        if row['published_at']:
            await reply(interaction, '후기를 등록했습니다.\n'
                f"https://discord.com/channels/{interaction.guild.id}/{row['channel_id']}/{row['message_id']}")
        else:
            await reply(interaction, '후기는 저장되었으며 채널 게시를 재시도하고 있습니다. 다시 작성하지 않아도 됩니다.')

    async def on_error(self, interaction, error):
        await report_error(interaction, error)
