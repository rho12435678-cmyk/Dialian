"""Persistent commission controls; private account inputs never echoed publicly."""
import discord

from ddh.blueprint import FIELDS
from ddh.commissions import available_developers, form_labels, progress, quote, save_account
from ddh.server import UserError, require_guild
from ddh.views import DecisionModal, GuardedView, reply, report_error


class CommissionEntryView(GuardedView):
    @discord.ui.button(label='작품 의뢰 시작', style=discord.ButtonStyle.primary, custom_id='ddh:commission:v1')
    async def commission(self, interaction, button):
        hub_id = await self.bot.store.get(interaction.guild.id, 'channel:commission_panel')
        hub = interaction.guild.get_channel(hub_id) if hub_id else None
        if hub and interaction.channel_id != hub.id:
            await reply(interaction, f'작품 의뢰 접수는 {hub.mention}으로 이동했습니다. 신청 방법·가격 안내를 확인하고 「작품 의뢰 시작」을 눌러 주세요.')
            return
        await interaction.response.send_message('원하는 개발 분야를 선택해 주세요.',
            view=CommissionFieldView(self.bot, interaction.user.id), ephemeral=True)


class CommissionFieldSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='의뢰할 개발 분야 선택', options=[
            discord.SelectOption(label=name, value=key) for key, name in FIELDS.items()])

    async def callback(self, interaction):
        if interaction.user.id != self.view.owner_id:
            raise UserError('본인의 의뢰 화면에서 선택해 주세요.')
        require_guild(interaction.guild, self.view.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        developers = await available_developers(self.view.bot, interaction.guild, self.values[0], requester_id=interaction.user.id)
        view = CommissionDeveloperView(self.view.bot, interaction.user.id, self.values[0], developers)
        await interaction.followup.send(view.prompt(), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class CommissionFieldView(GuardedView):
    def __init__(self, bot, owner_id):
        super().__init__(bot, timeout=300)
        self.owner_id = owner_id
        self.add_item(CommissionFieldSelect())


class CommissionDeveloperSelect(discord.ui.Select):
    def __init__(self, developers):
        super().__init__(placeholder='상담·제작을 맡길 개발자 선택', row=0,
            options=[discord.SelectOption(label=member.display_name[:100], value=str(member.id),
                                          description=f"@{member.name}"[:100]) for member in developers])

    async def callback(self, interaction):
        view = self.view
        if interaction.user.id != view.owner_id:
            raise UserError('본인의 의뢰 화면에서 선택해 주세요.')
        selected = int(self.values[0])
        if selected not in {member.id for member in view.developers}:
            raise UserError('목록에서 개발자를 다시 선택해 주세요.')
        await interaction.response.send_modal(CommissionModal(view.bot, view.field, selected))


class CommissionDeveloperView(GuardedView):
    def __init__(self, bot, owner_id, field, developers):
        super().__init__(bot, timeout=300)
        self.owner_id, self.field, self.developers = owner_id, field, developers
        self.page = 0
        self.refresh()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await reply(interaction, '본인의 의뢰 화면에서 선택해 주세요.')
            return False
        return await super().interaction_check(interaction)

    def prompt(self):
        if not self.developers:
            return f'현재 선택 가능한 {FIELDS[self.field]} 개발자가 없습니다. 아래 버튼으로 운영진에게 배정을 요청할 수 있습니다.'
        pages = (len(self.developers) + 24) // 25
        return (f'**{FIELDS[self.field]} 담당 개발자 선택** · {len(self.developers)}명 · {self.page + 1}/{pages}페이지\n'
                '담당자를 선택하고 신청서를 작성하면 해당 개발자와 대화할 비공개 티켓이 열립니다.')

    def refresh(self):
        for item in list(self.children):
            if isinstance(item, CommissionDeveloperSelect):
                self.remove_item(item)
        subset = self.developers[self.page * 25:(self.page + 1) * 25]
        if subset:
            self.add_item(CommissionDeveloperSelect(subset))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 25 >= len(self.developers)

    async def turn_page(self, interaction, direction):
        if interaction.user.id != self.owner_id:
            raise UserError('본인의 의뢰 화면에서 선택해 주세요.')
        self.page = min(max(self.page + direction, 0), max((len(self.developers) - 1) // 25, 0))
        self.refresh()
        await interaction.response.edit_message(content=self.prompt(), view=self, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='이전', row=1)
    async def previous(self, interaction, button):
        await self.turn_page(interaction, -1)

    @discord.ui.button(label='다음', row=1)
    async def next_page(self, interaction, button):
        await self.turn_page(interaction, 1)

    @discord.ui.button(label='운영진에게 배정 요청', row=1, style=discord.ButtonStyle.secondary)
    async def staff_assignment(self, interaction, button):
        if interaction.user.id != self.owner_id:
            raise UserError('본인의 의뢰 화면에서 선택해 주세요.')
        await interaction.response.send_modal(CommissionModal(self.bot, self.field))


class CommissionModal(discord.ui.Modal):
    def __init__(self, bot, field, developer_id=None):
        super().__init__(title=f'{FIELDS[field]} 작품 의뢰', timeout=600)
        self.bot, self.field, self.developer_id = bot, field, developer_id
        for index, label in enumerate(form_labels(field)):
            self.add_item(discord.ui.TextInput(label=label, max_length=100 if index == 0 else 900,
                                              required=index < 2, placeholder="링크가 없으면 비워 두고 티켓에 이미지·파일을 첨부해 주세요." if index == 2 else None, style=discord.TextStyle.paragraph if index else discord.TextStyle.short))

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        fields = {item.label: item.value.strip() for item in self.children}
        fields['_field'] = self.field
        fields['_developer'] = self.developer_id
        channel = await self.bot.tickets.create(interaction.guild, interaction.user, 'commission', fields)
        ticket = await self.bot.store.channel_ticket(interaction.guild.id, channel.id)
        assignment = f"담당 개발자: <@{ticket['assigned_id']}> · 이 티켓에서 신청서를 확인하고 상담·제작을 진행합니다." if ticket.get('assigned_id') else '운영진의 담당자 배정을 기다려 주세요.'
        await reply(interaction, f'의뢰 티켓: {channel.mention}\n{assignment}\n추가 이미지·파일은 티켓에 올려 주세요. 이미 진행 중인 의뢰가 있으면 기존 티켓으로 연결됩니다.')

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
