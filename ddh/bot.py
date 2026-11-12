import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from ddh.backups import backup
from ddh.blueprint import LAYOUT_VERSION, layout_summary
from ddh.cleanup import cleanup_legacy
from ddh.content import GUIDE_VERSION, publish_guide, publish_panels
from ddh.notices import publish_due
from ddh.operations import Operations, private_channel
from ddh.security import configure_security
from ddh.server import UserError, channel_for, is_admin, preflight, provision, require_guild, roles_for
from ddh.store import Store, utcnow
from ddh.tickets import Tickets
from ddh.updates import deployed_revision, publish_release
from ddh.verification import verify_member
from ddh.views import RulesView, SupportView, TicketActions, UpdateAlertsView, reply, report_error

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

    @app_commands.command(name="plan", description="DDH 채널·역할·권한 구성 미리보기")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def plan(self, interaction: discord.Interaction):
        await self.admin(interaction)
        await reply(interaction, layout_summary())

    @app_commands.command(name="setup", description="DDH 서버 구성. apply=True일 때 실제 적용합니다")
    @app_commands.describe(apply="True: 역할·채널·권한과 안내 패널을 실제 생성/갱신")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, apply: bool = False):
        await self.admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not apply:
            found = await preflight(interaction.guild, self.bot.store)
            await reply(interaction, layout_summary() + f"\n\n재사용 {sum(v is not None for v in found.values())}개 / "
                        f"새로 생성 {sum(v is None for v in found.values())}개. 적용: `/ddh setup apply:True`")
            return
        if self.bot.mutation_lock.locked():
            raise UserError("다른 서버 구성이 진행 중입니다. 잠시 후 다시 시도해 주세요.")
        counts, safeguards = await self.bot.apply_setup(interaction.guild, with_layout=True)
        try:
            await publish_release(self.bot, interaction.guild)
        except Exception:
            log.exception("DDH release baseline setup failed; will retry")
        roles, categories, channels = counts
        pending = await self.bot.store.get(interaction.guild.id, "role_order_pending", False)
        news_pending = await self.bot.store.get(interaction.guild.id, "news_format_pending", False)
        ordering = (
            "⚠️ 역할 정렬은 건너뛰었습니다. 서버 설정 → 역할에서 "
            "Dialian을 DDH 역할보다 위로 올려 주세요.\n"
            if pending else ""
        )
        announcement = (
            "⚠️ 공지사항·업데이트-공지의 공지형식 전환은 Discord 서버 설정에서 "
            "커뮤니티를 활성화한 뒤 재설정해 주세요.\n"
            if news_pending else ""
        )
        await reply(interaction, f"DDH 구성 완료: 역할 {roles}개 · 카테고리 {categories}개 · 채널 {channels}개.\n"
                    f"AutoMod 보안: {len(safeguards)}개 규칙 처리. 운영-로그 채널에서 차단 알림을 확인하세요.\n"
                    + ordering + announcement
                    + "구인·제작의뢰·포트폴리오는 포럼으로 운영됩니다.\n"
                    + "관리자/운영진 역할은 서버 소유자가 직접 지정합니다.")

    @app_commands.command(name="cleanup", description="소유자 확인 후 기존 티켓·보관 카테고리 영구 정리")
    @app_commands.describe(confirm="True: 기존 보관/종료 채널을 영구 삭제 (복구 불가능)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def cleanup(self, interaction: discord.Interaction, confirm: bool = False):
        require_guild(interaction.guild, self.bot.settings)
        if interaction.user.id != interaction.guild.owner_id:
            raise UserError("채널 영구 삭제는 서버 소유자만 승인할 수 있습니다.")
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
        await self.admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        _, safeguards = await self.bot.apply_setup(interaction.guild, with_layout=False)
        await reply(interaction, "보안 설정 적용: " + " / ".join(safeguards))

    @app_commands.command(name="panels", description="규칙·안내·문의 패널 복구 또는 갱신")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panels(self, interaction: discord.Interaction):
        await self.admin(interaction)
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
        await self.admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        granted = await verify_member(self.bot, interaction.guild, member.id, reviewer=interaction.user)
        await reply(interaction, "멤버 인증을 완료했습니다." if granted else "이미 인증된 멤버입니다.")

    @app_commands.command(name="notices", description="정기 DDH 안내 공지 켜기/끄기")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def notices(self, interaction: discord.Interaction, enabled: bool):
        await self.admin(interaction)
        await self.bot.store.set(interaction.guild.id, "notices_enabled", enabled)
        await reply(interaction, f"자동 공지 {'켜짐' if enabled else '꺼짐'} · "
                    f"한국 시간 {self.bot.settings.notice_hour}시 / {self.bot.settings.notice_interval_days}일 간격")

    @app_commands.command(name="status", description="Dialian 연결·구성 상태 확인")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        ready = await self.bot.store.get(interaction.guild.id, "setup_complete", False)
        notices = await self.bot.store.get(interaction.guild.id, "notices_enabled", self.bot.settings.notices_enabled)
        security = await self.bot.store.get(interaction.guild.id, "security_complete", False)
        role_order_pending = await self.bot.store.get(interaction.guild.id, "role_order_pending", False)
        news_pending = await self.bot.store.get(interaction.guild.id, "news_format_pending", False)
        await reply(interaction, f"Dialian · DDH\n서버 구성: {'완료' if ready else '미완료'}\n보안 설정: {'완료' if security else '미완료'}\n"
                    f"역할 정렬: {'수동 확인 필요' if role_order_pending else '정상'}\n"
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
        for view in (RulesView(self), SupportView(self), TicketActions(self), UpdateAlertsView(self)):
            self.add_view(view)
        target = discord.Object(id=self.settings.guild_id)
        self.tree.add_command(DDHCommands(self), guild=target)
        await self.tree.sync(guild=target)
        self.maintenance.start()

    async def apply_setup(self, guild, *, with_layout):
        """Repeatable DDH provisioning + native security; no unrelated resources deleted."""
        require_guild(guild, self.settings)
        async with self.mutation_lock:
            if with_layout:
                counts = await provision(guild, self.store, self.settings)
                await publish_panels(self, guild)
                await self.store.set(guild.id, "release:layout_ready", True)
                await self.store.set(guild.id, "layout:version", LAYOUT_VERSION)
            else:
                if not await self.store.get(guild.id, "setup_complete", False):
                    raise UserError("먼저 /ddh setup apply:True로 서버 구성을 완료해 주세요.")
                counts = (0, 0, 0)
            roles = await roles_for(guild, self.store)
            await self.store.set(guild.id, "setup_complete", True)
            await self.store.set(guild.id, "security_complete", False)
            audit = await channel_for(guild, self.store, "security_log")
            if not private_channel(audit, guild, {roles["staff"].id, roles["admin"].id, guild.me.id}):
                raise UserError("보안 로그 채널의 비공개 권한을 복구한 뒤 보안 설정을 적용해 주세요.")
            safeguards = await configure_security(guild, staff_role=roles["staff"], admin_role=roles["admin"],
                                                   audit_channel=audit)
            await self.store.set(guild.id, "security_complete", True)
            if not await self.store.get(guild.id, "setup_at"):
                await self.store.set(guild.id, "setup_at", utcnow())
            return counts, safeguards

    async def on_ready(self):
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("Configured DDH guild is unavailable; check DDH_GUILD_ID and bot invitation")
            return
        log.info("Dialian connected to DDH guild %s", guild.id)
        if self.settings.auto_setup and (
            not await self.store.get(guild.id, "setup_complete", False)
            or not await self.store.get(guild.id, "security_complete", False)
            or not await self.store.get(guild.id, "release:layout_ready", False)
            or await self.store.get(guild.id, "layout:version") != LAYOUT_VERSION
        ):
            try:
                needs_layout = (
                    not await self.store.get(guild.id, "setup_complete", False)
                    or not await self.store.get(guild.id, "release:layout_ready", False)
                    or await self.store.get(guild.id, "layout:version") != LAYOUT_VERSION
                )
                await self.apply_setup(guild, with_layout=needs_layout)
                log.info("DDH automatic provisioning and AutoMod protections applied")
            except Exception:
                log.exception("DDH automatic setup failed; check roles, permissions and resource conflicts")
        # Keep the official guide current after a deployment, without
        # creating another message for returning users.
        if await self.store.get(guild.id, "setup_complete", False):
            try:
                async with self.mutation_lock:
                    if await self.store.get(guild.id, "guide:version") != GUIDE_VERSION:
                        await publish_guide(self, guild)
                        log.info("DDH first-visit guide refreshed")
            except Exception:
                log.exception("DDH first-visit guide refresh failed; use /ddh panels to retry")
        try:
            await publish_release(self, guild)
        except Exception:
            log.exception("DDH release announcement failed; maintenance will retry")

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
                                 ("scheduled notice", lambda g: publish_due(self, g)),
                                 ("release announcement", lambda g: publish_release(self, g))):
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
            if (await self.store.get(guild.id, "layout:version") == LAYOUT_VERSION
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
