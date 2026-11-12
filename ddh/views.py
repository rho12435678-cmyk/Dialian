"""Persistent public panels; every callback independently checks guild and authority."""
import logging

import discord

from ddh.blueprint import FIELDS, KINDS
from ddh.server import UserError, is_staff, require_guild

from ddh.verification import verify_member

log = logging.getLogger(__name__)


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def report_error(interaction, error):
    if isinstance(error, UserError):
        text = str(error)
    elif isinstance(error, discord.Forbidden):
        text = "봇 권한이나 역할 순서가 부족합니다. 운영진에게 확인을 요청해 주세요."
    else:
        text = "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. 운영진은 봇 로그를 확인해 주세요."
    if not isinstance(error, UserError):
        log.error("DDH interaction failed (%s)", type(error).__name__, exc_info=(type(error), error, error.__traceback__))
    await reply(interaction, text)


class GuardedView(discord.ui.View):
    def __init__(self, bot, *, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def interaction_check(self, interaction):
        try:
            require_guild(interaction.guild, self.bot.settings)
            await self.bot.tickets.ready(interaction.guild)
            return True
        except UserError as error:
            await reply(interaction, str(error))
            return False

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


class RulesView(GuardedView):
    async def on_error(self, interaction, error, item):
        # The earlier generic "interaction failed" message was not enough
        # to identify live account/role errors. Keep only a safe owner-only
        # diagnostic in SQLite, never member identifiers or Discord tokens.
        if interaction.guild is not None:
            problem = str(error) if isinstance(error, UserError) else (
                f"인증 버튼 내부 오류: {type(error).__name__} · 봇 로그 확인"
            )
            try:
                await self.bot.store.set(
                    interaction.guild.id, "verification:last_error", problem[:240],
                )
            except Exception:
                log.exception("Could not persist DDH verification diagnosis")
        await report_error(interaction, error, item)

    @discord.ui.button(label="규칙 동의 · 멤버 인증", style=discord.ButtonStyle.success, custom_id="ddh:rules:v1")
    async def accept(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        # The official persistent DDH button can also be displayed in a
        # Discord Server Guide resource page. Its interaction.channel_id may
        # differ from the stored rules text channel; that must not block joining.
        # The server/role/account checks inside verify_member still apply.
        granted = await verify_member(self.bot, interaction.guild, interaction.user.id)
        await self.bot.store.set(interaction.guild.id, "verification:last_error", None)
        await reply(interaction, "멤버 인증 완료. 채팅·포럼·음성 채널을 이용할 수 있습니다." if granted
                    else "이미 인증된 멤버입니다.")


class SupportView(GuardedView):
    @discord.ui.button(label="파트너 문의", style=discord.ButtonStyle.primary, custom_id="ddh:partner:v1")
    async def partner(self, interaction, button):
        await interaction.response.send_modal(TicketModal(self.bot, "partner"))

    @discord.ui.button(label="서버 피드백", style=discord.ButtonStyle.secondary, custom_id="ddh:feedback:v1")
    async def feedback(self, interaction, button):
        await interaction.response.send_modal(TicketModal(self.bot, "feedback"))

    @discord.ui.button(label="거래·도용 신고", style=discord.ButtonStyle.danger,
                       custom_id="ddh:report:v1")
    async def report(self, interaction, button):
        await interaction.response.send_modal(ReportModal(self.bot))

    @discord.ui.button(label="개발자 신청", style=discord.ButtonStyle.success, custom_id="ddh:developer:v1")
    async def developer(self, interaction, button):
        await interaction.response.send_message("지원할 분야를 모두 선택해 주세요.",
                                                view=FieldView(self.bot, interaction.user.id), ephemeral=True)


class ReportModal(discord.ui.Modal):
    """Private reports use the existing feedback ticket kind and DB schema."""

    def __init__(self, bot):
        super().__init__(title="DDH 거래·도용 신고", timeout=600)
        self.bot = bot
        self.subject = discord.ui.TextInput(
            label="신고 대상 닉네임·게시글", max_length=100,
            placeholder="상대방 Discord 표시 이름 또는 게시글 제목",
        )
        self.details = discord.ui.TextInput(
            label="문제 상황·발생일·요청 사항", max_length=900,
            style=discord.TextStyle.paragraph,
            placeholder="작업 조건, 실제 상황, 관련 메시지 등 사실을 적어 주세요.",
        )
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        subject, details = self.subject.value.strip(), self.details.value.strip()
        if not subject or not details:
            raise UserError("신고 대상과 문제 상황을 모두 입력해 주세요.")
        current = await self.bot.store.active(interaction.guild.id, interaction.user.id, "feedback")
        if current:
            channel = interaction.guild.get_channel(current.get("channel_id", 0))
            target = channel.mention if channel is not None else "진행 중 피드백 티켓"
            raise UserError(
                f"이미 진행 중인 피드백/신고 티켓이 있습니다: {target}. "
                "그 채널에 새 신고 내용과 증거를 추가해 주세요."
            )
        channel = await self.bot.tickets.create(
            interaction.guild, interaction.user, "feedback",
            {"제목": "[거래·작품 신고] " + subject, "내용": details},
        )
        await reply(
            interaction,
            f"비공개 신고 접수: {channel.mention}\n"
            "해당 채널에 관련 메시지·게시글 증거를 추가할 수 있습니다. "
            "비밀번호·인증코드·결제 정보는 공유하지 마세요. "
            "DDH는 DM 거래를 직접 중개하거나 환불을 보증하지 않습니다.",
        )

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class FieldSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="개발 분야 선택 (복수 선택 가능)", min_values=1, max_values=len(FIELDS),
                         options=[discord.SelectOption(label=name, value=key) for key, name in FIELDS.items()])

    async def callback(self, interaction):
        if interaction.user.id != self.view.owner_id:
            raise UserError("본인이 시작한 신청 화면에서 선택해 주세요.")
        await interaction.response.send_modal(TicketModal(self.view.bot, "developer", list(self.values)))


class FieldView(GuardedView):
    def __init__(self, bot, owner_id):
        super().__init__(bot, timeout=300)
        self.owner_id = owner_id
        self.add_item(FieldSelect())


class TicketModal(discord.ui.Modal):
    def __init__(self, bot, kind, fields=None):
        super().__init__(title=KINDS[kind], timeout=600)
        self.bot, self.kind, self.fields = bot, kind, fields
        specs = {
            "partner": [("서버 이름", 100), ("초대 링크", 300), ("서버 소개·제휴 제안", 1000)],
            "feedback": [("제목", 100), ("내용", 1000)],
            "developer": [("Roblox 사용자명·프로필", 300), ("포트폴리오 링크", 1000),
                          ("경력·도구·본인 담당 부분", 1000), ("소개·활동 가능 시간", 500)],
        }[kind]
        for label, length in specs:
            self.add_item(discord.ui.TextInput(label=label, max_length=length,
                                              style=discord.TextStyle.paragraph if length > 300 else discord.TextStyle.short))

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        fields = {item.label: item.value.strip() for item in self.children}
        if self.fields:
            fields["_fields"] = self.fields
        channel = await self.bot.tickets.create(interaction.guild, interaction.user, self.kind, fields)
        await reply(interaction, f"문의 티켓: {channel.mention}\n이미 진행 중인 같은 종류의 문의가 있으면 해당 티켓으로 연결됩니다.")

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class DecisionModal(discord.ui.Modal):
    def __init__(self, bot, action, channel_id):
        super().__init__(title={"approve": "신청 승인", "reject": "신청 반려", "close": "티켓 종료"}[action], timeout=300)
        self.bot, self.action, self.channel_id = bot, action, channel_id
        self.reason = discord.ui.TextInput(label="처리 사유", style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        if interaction.channel_id != self.channel_id:
            raise UserError("티켓 채널이 일치하지 않습니다.")
        if not self.reason.value.strip():
            raise UserError("처리 사유를 입력해 주세요.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.action == "close":
            await self.bot.tickets.close(interaction.guild, interaction.channel, interaction.user, self.reason.value.strip())
            await reply(interaction, "처리 결과를 기록하고 티켓 채널을 종료했습니다.")
        else:
            label = await self.bot.tickets.review(interaction.guild, interaction.channel, interaction.user,
                                                  self.action == "approve", self.reason.value.strip())
            await reply(interaction, f"신청을 {label}했습니다.")

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class TicketActions(GuardedView):
    async def launch(self, interaction, action):
        ticket = await self.bot.tickets.current(interaction.guild, interaction.channel_id)
        staff = await is_staff(interaction.user, self.bot.store)
        if action != "close" and not staff:
            raise UserError("운영진만 신청을 심사할 수 있습니다.")
        if action == "close" and not staff and interaction.user.id != ticket["owner_id"]:
            raise UserError("신청자 또는 운영진만 티켓을 종료할 수 있습니다.")
        eligible = ticket["decision"] == "pending" or (action == "approve" and ticket["decision"] == "approving")
        if action != "close" and (ticket["kind"] == "feedback" or not eligible):
            raise UserError("심사 대상이 아니거나 이미 처리된 신청입니다.")
        await interaction.response.send_modal(DecisionModal(self.bot, action, interaction.channel_id))

    @discord.ui.button(label="승인 (운영진)", style=discord.ButtonStyle.success, custom_id="ddh:approve:v1")
    async def approve(self, interaction, button):
        await self.launch(interaction, "approve")

    @discord.ui.button(label="반려 (운영진)", style=discord.ButtonStyle.secondary, custom_id="ddh:reject:v1")
    async def reject(self, interaction, button):
        await self.launch(interaction, "reject")

    @discord.ui.button(label="티켓 종료", style=discord.ButtonStyle.danger, custom_id="ddh:close:v1")
    async def close_ticket(self, interaction, button):
        await self.launch(interaction, "close")

