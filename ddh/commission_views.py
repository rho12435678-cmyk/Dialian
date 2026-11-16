"""Persistent commission controls; private account inputs never echoed publicly."""
import discord

from ddh.blueprint import FIELDS
from ddh.commissions import form_labels, progress, quote, save_account
from ddh.server import UserError, require_guild
from ddh.views import DecisionModal, GuardedView, reply, report_error


class CommissionFieldSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='의뢰할 개발 분야 선택', options=[
            discord.SelectOption(label=name, value=key) for key, name in FIELDS.items()])

    async def callback(self, interaction):
        if interaction.user.id != self.view.owner_id:
            raise UserError('본인의 의뢰 화면에서 선택해 주세요.')
        await interaction.response.send_modal(CommissionModal(self.view.bot, self.values[0]))


class CommissionFieldView(GuardedView):
    def __init__(self, bot, owner_id):
        super().__init__(bot, timeout=300)
        self.owner_id = owner_id
        self.add_item(CommissionFieldSelect())


class CommissionModal(discord.ui.Modal):
    def __init__(self, bot, field):
        super().__init__(title=f'{FIELDS[field]} 작품 의뢰', timeout=600)
        self.bot, self.field = bot, field
        for index, label in enumerate(form_labels(field)):
            self.add_item(discord.ui.TextInput(label=label, max_length=100 if index == 0 else 900,
                                              required=index != 4, style=discord.TextStyle.paragraph if index else discord.TextStyle.short))

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        fields = {item.label: item.value.strip() for item in self.children}
        fields['_field'] = self.field
        channel = await self.bot.tickets.create(interaction.guild, interaction.user, 'commission', fields)
        await reply(interaction, f'의뢰 접수: {channel.mention}\n추가 이미지·파일은 티켓에 올려 주세요. 담당자 배정 전에는 제작이 확정되지 않습니다.')

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class AccountModal(discord.ui.Modal):
    def __init__(self, bot):
        super().__init__(title='개발자 본인 계좌 등록·수정', timeout=300)
        self.bot = bot
        self.bank = discord.ui.TextInput(label='은행명', max_length=60)
        self.account = discord.ui.TextInput(label='계좌번호 (본인 명의)', min_length=6, max_length=40)
        self.holder = discord.ui.TextInput(label='예금주', max_length=60)
        for item in (self.bank, self.account, self.holder):
            self.add_item(item)

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await save_account(self.bot, interaction.guild, interaction.user, self.bank.value, self.account.value, self.holder.value)
        await reply(interaction, '본인 계좌를 저장했습니다. 담당 의뢰의 「견적·계좌 안내」를 누를 때 해당 티켓에만 표시합니다. 삭제는 `/ddh account-remove`를 사용하세요.')

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class QuoteModal(discord.ui.Modal):
    def __init__(self, bot, channel_id):
        super().__init__(title='의뢰 견적·계좌 안내', timeout=300)
        self.bot, self.channel_id = bot, channel_id
        self.amount = discord.ui.TextInput(label='합의할 금액 (원)', max_length=12)
        self.terms = discord.ui.TextInput(label='작업 범위·납기·수정·취소 조건', max_length=1000, style=discord.TextStyle.paragraph)
        self.add_item(self.amount)
        self.add_item(self.terms)

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        if interaction.channel_id != self.channel_id:
            raise UserError('원래 의뢰 티켓에서 실행해 주세요.')
        await interaction.response.defer(ephemeral=True, thinking=True)
        await quote(self.bot, interaction.guild, interaction.channel, interaction.user, self.amount.value, self.terms.value)
        await reply(interaction, '이 티켓에 견적과 본인 계좌를 안내했습니다. 입금 여부는 직접 확인해 주세요.')

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class CommissionActions(GuardedView):
    @discord.ui.button(label='견적·계좌 안내', custom_id='ddh:commission:quote:v1', style=discord.ButtonStyle.primary)
    async def send_quote(self, interaction, button):
        ticket = await self.bot.tickets.current(interaction.guild, interaction.channel_id)
        if ticket.get('assigned_id') != interaction.user.id:
            raise UserError('이 의뢰의 담당 개발자만 계좌를 안내할 수 있습니다.')
        await interaction.response.send_modal(QuoteModal(self.bot, interaction.channel_id))

    async def change_stage(self, interaction, stage):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await progress(self.bot, interaction.guild, interaction.channel, interaction.user, stage)
        await reply(interaction, '의뢰 진행 상태를 갱신했습니다.')

    @discord.ui.button(label='제작 시작', custom_id='ddh:commission:working:v1')
    async def working(self, interaction, button):
        await self.change_stage(interaction, 'working')

    @discord.ui.button(label='검수·수정', custom_id='ddh:commission:revision:v1')
    async def revision(self, interaction, button):
        await self.change_stage(interaction, 'revision')

    @discord.ui.button(label='납품 완료', custom_id='ddh:commission:delivered:v1', style=discord.ButtonStyle.success)
    async def delivered(self, interaction, button):
        await self.change_stage(interaction, 'delivered')

    @discord.ui.button(label='의뢰 종료', custom_id='ddh:commission:close:v1', style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction, button):
        from ddh.commissions import manager
        ticket = await self.bot.tickets.current(interaction.guild, interaction.channel_id)
        await manager(self.bot, interaction.guild, interaction.user, ticket)
        await interaction.response.send_modal(DecisionModal(self.bot, 'close', interaction.channel_id))
