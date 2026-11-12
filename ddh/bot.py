import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from ddh.backups import backup
from ddh.blueprint import CATEGORIES, CHANNELS, LAYOUT_VERSION, layout_summary
from ddh.cleanup import cleanup_legacy
from ddh.content import publish_panels, publish_verification
from ddh.layout import cosmetic_layout
from ddh.notices import OWNER_NOTICES_KEY, publish_due
from ddh.operations import Operations, private_channel
from ddh.security import configure_security
from ddh.server import (
    UserError, channel_for, create_text_board, find_resource, is_admin, preflight, provision, require_guild, roles_for,
)
from ddh.store import Store, utcnow
from ddh.tickets import Tickets
from ddh.updates import deployed_revision, publish_release
from ddh.verification import verify_member
from ddh.views import RulesView, SupportView, TicketActions, UpdateAlertsView, VerificationView, reply, report_error

log = logging.getLogger(__name__)


class DDHCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="ddh", description="Dial Dev Hub 서버 관리·안내")
        self.bot = bot

    async def admin(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        admin_id = await self.bot.store.get(interaction.guild.id, "role:admin", 0)
        permitted = is_admin(interaction.user) or any(
            role.id == admin_id for role in getattr(interaction.user, "roles", ())
        )
        if not permitted:
            raise UserError("서버 소유자 또는 DDH 관리자 역할만 사용할 수 있습니다.")

    def owner(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        if interaction.user.id != interaction.guild.owner_id:
            raise UserError("이 명령은 Discord 서버 소유자만 실행할 수 있습니다.")

    @app_commands.command(name="plan", description="DDH 채널·역할·권한 구성 미리보기")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def plan(self, interaction: discord.Interaction):
        await self.admin(interaction)
        await reply(interaction, layout_summary())

    @app_commands.command(name="setup", description="소유자가 선택하여 없는 초기 구성만 생성")
    @app_commands.describe(apply="True: 누락된 초기 역할·카테고리·채널 생성")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, apply: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not apply:
            found = await preflight(interaction.guild, self.bot.store)
            await reply(interaction, layout_summary() + f"\n\n기존 항목 {sum(v is not None for v in found.values())}개 / "
                        f"새로 생성 {sum(v is None for v in found.values())}개.")
            return
        await self.bot.bootstrap(interaction.guild)
        await reply(interaction, "초기 구성을 완료했습니다. 기존 항목을 재사용하고 누락된 항목만 생성했습니다.")

    @app_commands.command(name="layout", description="소유자가 가이드·질문·아이디어·자료 채널 정리")
    @app_commands.describe(apply="True: 포럼 정리 및 자료 채널 영구 삭제")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def layout(self, interaction: discord.Interaction, apply: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            summary = await cosmetic_layout(interaction.guild, self.bot.store, apply=apply)
        await reply(interaction, summary)

    @app_commands.command(name="onboarding", description="인증 채널과 비공개 입퇴장 채널 준비")
    @app_commands.describe(apply="True: 두 채널을 생성 또는 연결하고 인증 버튼 게시")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def onboarding(self, interaction: discord.Interaction, apply: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not apply:
            await reply(interaction, "안내에 인증-verify, Staff에 비공개 입퇴장-join-leave를 연결합니다. "
                        "`/ddh onboarding apply:True`로 적용하세요.")
            return
        async with self.bot.mutation_lock:
            await self.bot.tickets.ready(guild)
            roles = await roles_for(guild, self.bot.store, keys=("staff", "admin", "member"))
            plans = []
            for spec in CHANNELS:
                if spec.key not in ("verification", "member_log"):
                    continue
                channel = await find_resource(guild, self.bot.store, f"channel:{spec.key}", spec.name,
                                              discord.TextChannel, spec.aliases)
                category = None
                if channel is None:
                    parent = next(category for category in CATEGORIES if category.key == spec.category)
                    category = await find_resource(guild, self.bot.store, f"category:{parent.key}", parent.name,
                                                   discord.CategoryChannel, parent.aliases)
                    if category is None:
                        raise UserError(f"{parent.name} 카테고리를 찾지 못했습니다.")
                    if len(category.channels) >= 50:
                        raise UserError(f"{parent.name} 카테고리에 채널 공간을 확보해 주세요.")
                if spec.key == "member_log" and channel is not None and not private_channel(
                    channel, guild, {roles["staff"].id, roles["admin"].id, guild.me.id}
                ):
                    raise UserError("입퇴장 채널을 Staff 전용으로 제한한 뒤 다시 실행해 주세요.")
                plans.append((spec, channel, category))
            mentions = []
            for spec, channel, category in plans:
                if channel is None:
                    channel = await create_text_board(
                        guild, spec, category, roles, reason="DDH 소유자 요청: 인증·입퇴장 채널")
                await self.bot.store.set(guild.id, f"channel:{spec.key}", channel.id)
                mentions.append(channel.mention)
            await publish_verification(self.bot, guild)
        note = "" if self.bot.intents.members else (
            "\n입퇴장 기록을 시작하려면 Developer Portal의 Server Members Intent를 켜고 재시작해 주세요."
        )
        await reply(interaction, "설치 완료 · " + " · ".join(mentions) + note)

    @app_commands.command(name="cleanup", description="소유자 확인 후 기존 티켓·보관 카테고리 영구 정리")
    @app_commands.describe(confirm="True: 기존 보관/종료 채널을 영구 삭제 (복구 불가능)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def cleanup(self, interaction: discord.Interaction, confirm: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.bot.mutation_lock.locked():
            raise UserError("다른 서버 변경 작업이 진행 중입니다.")
        async with self.bot.mutation_lock:
            summary = await cleanup_legacy(
                interaction.guild, self.bot.store, self.bot.settings,
                confirm=confirm,
            )
        await reply(interaction, summary)

    @app_commands.command(name="security", description="기존 채널을 변경하지 않고 DDH 보안 설정 재적용")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def security(self, interaction: discord.Interaction):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        safeguards = await self.bot.apply_security(interaction.guild)
        await reply(interaction, "보안 설정 적용: " + " / ".join(safeguards))

    @app_commands.command(name="panels", description="규칙·안내·문의 패널 복구 또는 갱신")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panels(self, interaction: discord.Interaction):
        self.owner(interaction)
        await self.bot.tickets.ready(interaction.guild)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            await publish_panels(self.bot, interaction.guild)
        await reply(interaction, "DDH 안내 패널을 갱신했습니다.")

    @app_commands.command(name="check", description="역할·포럼·비공개 권한과 입퇴장 기록 설정 점검")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def check(self, interaction: discord.Interaction):
        await self.admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        findings = await self.bot.operations.findings(interaction.guild)
        if not self.bot.intents.members:
            findings.insert(0, "입퇴장 로그 꺼짐 · Developer Portal에서 Server Members Intent 활성화 후 재시작")
        state = "점검 완료 · 불일치 없음" if not findings else (
            f"점검 항목 {len(findings)}건\n" + "\n".join(f"• {item}" for item in findings[:12])
        )
        await reply(interaction, state)

    @app_commands.command(name="verify", description="관리자가 계정 연령 예외를 확인한 멤버를 수동 인증")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def verify(self, interaction: discord.Interaction, member: discord.Member):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        granted = await verify_member(self.bot, interaction.guild, member.id, reviewer=interaction.user)
        await reply(interaction, "멤버 인증을 완료했습니다." if granted else "이미 인증된 멤버입니다.")

    @app_commands.command(name="notices", description="소유자가 정기 안내 공지 켜기 또는 끄기")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def notices(self, interaction: discord.Interaction, enabled: bool):
        self.owner(interaction)
        await self.bot.store.set(interaction.guild.id, OWNER_NOTICES_KEY, enabled)
        await reply(interaction, f"자동 공지 {'켜짐' if enabled else '꺼짐'} · "
                    f"한국 시간 {self.bot.settings.notice_hour}시 / {self.bot.settings.notice_interval_days}일 간격")

    @app_commands.command(name="release", description="소유자가 현재 배포 버전의 업데이트 공지를 게시")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def release(self, interaction: discord.Interaction):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        published = await publish_release(self.bot, interaction.guild)
        await reply(interaction, "업데이트 공지를 게시했습니다." if published else "게시할 새 업데이트가 없습니다.")

    @app_commands.command(name="status", description="Dialian 연결·구성 상태 확인")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        ready = await self.bot.store.get(interaction.guild.id, "setup_complete", False)
        notices = await self.bot.store.get(interaction.guild.id, OWNER_NOTICES_KEY, False)
        security = await self.bot.store.get(interaction.guild.id, "security_complete", False)
        news_pending = await self.bot.store.get(interaction.guild.id, "news_format_pending", False)
        await reply(interaction, f"Dialian · DDH\n서버 구성: {'완료' if ready else '미완료'}\n보안 설정: {'완료' if security else '미완료'}\n"
                    f"공지형식: {'커뮤니티 활성화 필요' if news_pending else '설정 완료'}\n"
                    f"자동 공지: {'켜짐' if notices else '꺼짐'}\n시작 시각: {self.bot.started_at}\n"
                    "문의는 문의·신청 패널을 이용해 주세요.")


class Dialian(discord.Client):
    def __init__(self, settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = settings.member_events
        intents.moderation = True
        super().__init__(intents=intents, member_cache_flags=discord.MemberCacheFlags.none(),
                         chunk_guilds_at_startup=False, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database)
        self.mutation_lock = asyncio.Lock()
        self.operations = Operations(self)
        self.tickets = Tickets(self)
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.command_error
        self.started_at = utcnow()
        self.last_backup_day = None
        self.release_sha = deployed_revision()
        self.update_lock = asyncio.Lock()
        self.last_policy_check = None

    async def setup_hook(self):
        await self.store.initialize()
        await backup(self.settings.database)
        self.last_backup_day = datetime.now(timezone.utc).date()
        for view in (RulesView(self), SupportView(self), TicketActions(self), UpdateAlertsView(self),
                     VerificationView(self)):
            self.add_view(view)
        target = discord.Object(id=self.settings.guild_id)
        self.tree.add_command(DDHCommands(self), guild=target)
        await self.tree.sync(guild=target)
        self.maintenance.start()

    async def bootstrap(self, guild):
        require_guild(guild, self.settings)
        async with self.mutation_lock:
            ready = await self.store.get(guild.id, "setup_complete", False)
            await provision(guild, self.store, self.settings)
            if not ready:
                await publish_panels(self, guild)
            await self.store.set(guild.id, "layout:version", LAYOUT_VERSION)
            if not await self.store.get(guild.id, "setup_at"):
                await self.store.set(guild.id, "setup_at", utcnow())
            await self.store.set(guild.id, "setup_complete", True)

    async def apply_security(self, guild):
        require_guild(guild, self.settings)
        async with self.mutation_lock:
            await self.tickets.ready(guild)
            roles = await roles_for(guild, self.store, keys=("staff", "admin"))
            audit = await channel_for(guild, self.store, "security_log")
            if not private_channel(audit, guild, {roles["staff"].id, roles["admin"].id, guild.me.id}):
                raise UserError("보안 로그 채널의 비공개 권한을 확인한 뒤 보안 설정을 적용해 주세요.")
            await self.store.set(guild.id, "security_complete", False)
            safeguards = await configure_security(guild, staff_role=roles["staff"], admin_role=roles["admin"],
                                                   audit_channel=audit)
            await self.store.set(guild.id, "security_complete", True)
            return safeguards

    async def on_ready(self):
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("Configured DDH guild is unavailable; check DDH_GUILD_ID and bot invitation")
            return
        log.info("Dialian connected to DDH guild %s", guild.id)

    async def on_member_join(self, member):
        await self.operations.membership(member.guild, member, joined=True)

    async def on_raw_member_remove(self, payload):
        guild = self.get_guild(payload.guild_id)
        if guild is not None:
            await self.operations.membership(guild, payload.user, joined=False)

    async def on_audit_log_entry_create(self, entry):
        await self.operations.audit(entry)

    async def on_guild_channel_delete(self, channel):
        if channel.guild.id != self.settings.guild_id:
            return
        async with self.mutation_lock:
            ticket = await self.store.channel_ticket(channel.guild.id, channel.id)
            if ticket and ticket["state"] in ("creating", "open"):
                await self.store.update_ticket(ticket["id"], channel.guild.id, state="deleted")

    async def command_error(self, interaction, error):
        await report_error(interaction, getattr(error, "original", error))

    @tasks.loop(seconds=60)
    async def maintenance(self):
        try:
            await self.maintenance_once()
        except Exception:
            # A transient SQLite/cache error must not permanently stop the loop.
            log.exception("DDH maintenance failed; will retry")

    async def maintenance_once(self):
        guild = self.get_guild(self.settings.guild_id)
        if guild and await self.store.get(guild.id, "setup_complete", False):
            for name, action in (("ticket recovery", self.tickets.recover),
                                 ("scheduled notice", lambda g: publish_due(self, g))):
                try:
                    await action(guild)
                except Exception:
                    log.exception("DDH %s failed; will retry", name)
        now = datetime.now(timezone.utc)
        if guild:
            # Gateway events are backed up by a cursor over Discord audit history.
            try:
                await self.operations.catch_up(guild)
            except Exception:
                log.exception("DDH Discord audit catch-up failed; will retry")
            if (await self.store.get(guild.id, "setup_complete", False)
                    and (self.last_policy_check is None
                         or (now - self.last_policy_check).total_seconds() >= 300)
                    and not self.mutation_lock.locked()):
                await self.operations.check(guild)
                self.last_policy_check = now
            await self.operations.flush(guild)
        today = now.date()
        if self.last_backup_day != today:
            try:
                await backup(self.settings.database)
                if guild:
                    await self.store.purge_delivered_events(guild.id, (now - timedelta(days=30)).isoformat())
                self.last_backup_day = today
            except Exception:
                log.exception("DDH backup failed; will retry")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.wait_until_ready()

    async def close(self):
        self.maintenance.cancel()
        await super().close()
