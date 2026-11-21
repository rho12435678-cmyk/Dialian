import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from ddh import __version__
from ddh.activity import repair_activity
from ddh.backups import backup
from ddh.blueprint import LAYOUT_VERSION, layout_summary, overwrites
from ddh.cleanup import cleanup_legacy
from ddh.content import publish_panels
from ddh.layout import cosmetic_layout
from ddh.notices import OWNER_NOTICES_KEY, publish_due
from ddh.operations import Operations, private_channel
from ddh.permissions import operator_migration
from ddh.security import configure_security
from ddh.server import UserError, channel_for, is_admin, preflight, provision, require_guild, roles_for
from ddh.store import Store, utcnow
from ddh.tickets import Tickets
from ddh.updates import RELEASE_NOTICES_KEY, deployed_revision, publish_release, release_announcements_allowed
from ddh.verification import verify_member
from ddh.views import RulesView, SupportView, TicketActions, reply, report_error
from ddh.visibility import apply_visibility
from ddh.commission_views import AccountModal, CommissionActions, CommissionEntryView, open_commission_close
from ddh.commissions import account_actor, assign, install, progress, remove_account, send_account
from ddh.reviews import configure_reviews, recover_reviews, request_review_by_command
from ddh.review_views import ReviewReceiptView, ReviewRequestView, open_review
from ddh.readability import apply_requested_copy

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

    @app_commands.command(name="operator", description="기존 운영자 역할에 DDH 관리 권한 이전")
    @app_commands.describe(apply="True: 이전 운영진 역할의 채널 접근을 운영자에게 이전")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def operator(self, interaction: discord.Interaction, apply: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            result = await operator_migration(
                interaction.guild, self.bot.store, apply=apply
            )
        await reply(interaction, result)

    @app_commands.command(name="visibility", description="소유자 확인 후 미인증자에게 인증 채널만 표시")
    @app_commands.describe(
        apply="True: 이 요청에 한 번만 채널 보기 권한 변경",
        verification_channel="이미 사용 중인 인증 텍스트 채널",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def visibility(
        self, interaction: discord.Interaction, apply: bool = False,
        verification_channel: discord.TextChannel | None = None,
    ):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            summary = await apply_visibility(
                self.bot, interaction.guild, apply=apply,
                verification_channel=verification_channel,
            )
        await reply(interaction, summary)

    @app_commands.command(name="activity", description="포트폴리오 분리 및 커뮤니티 게시·댓글·파일 권한 정리")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def activity(self, interaction: discord.Interaction, apply: bool = False):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            summary = await repair_activity(self.bot, interaction.guild, apply=apply)
        await reply(interaction, summary)

    @app_commands.command(name="commissions", description="소유자가 의뢰 전용 안내·접수 채널을 만들고 문의와 분리")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def commissions(self, interaction: discord.Interaction):
        self.owner(interaction)
        await self.bot.tickets.ready(interaction.guild)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            result = await install(self.bot, interaction.guild)
        await reply(interaction, result)

    @app_commands.command(name="logs", description="소유자가 운영·보안 로그 채널의 비공개 권한과 안내를 적용")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def logs(self, interaction: discord.Interaction):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            roles = await roles_for(interaction.guild, self.bot.store)
            targets = [(await channel_for(interaction.guild, self.bot.store, key), topic) for key, topic in (
                ("audit", "운영 기록 · 입퇴장 / 문의·의뢰 / 담당자 / 진행 상황 / 일반 설정 변경"),
                ("security_log", "보안 확인 · 권한 변경 / 제재 / 주요 설정 변경 / 활동 급증 · 정상 관리자 작업도 포함"))]
            for channel, topic in targets:
                await channel.edit(overwrites=overwrites(interaction.guild, roles, "log"), topic=topic,
                                   reason="DDH 소유자 요청: 운영·보안 로그 비공개 권한 정리")
        await self.bot.operations.flush(interaction.guild)
        await reply(interaction, "기존 운영·보안 로그 채널을 운영진 전용 읽기 권한으로 정리했습니다. 앞으로 활동과 보안 변경을 구분하여 기록합니다.")

    @app_commands.command(name="assign", description="운영진이 이 의뢰 티켓에 해당 분야 개발자 배정")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def assign_developer(self, interaction: discord.Interaction, developer: discord.Member):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await assign(self.bot, interaction.guild, interaction.channel, interaction.user, developer)
        await reply(interaction, "담당 개발자를 배정했습니다. 개발자는 이 티켓에서 견적·계좌 안내와 진행 상태를 관리할 수 있습니다.")

    @app_commands.command(name="account", description="본인 계좌 등록·수정 또는 운영진의 개발자 계좌 대리 등록")
    @app_commands.describe(developer="운영진 대리 등록 대상 개발자 (생략하면 본인)")
    @app_commands.guild_only()
    async def account(self, interaction: discord.Interaction, developer: discord.Member | None = None):
        require_guild(interaction.guild, self.bot.settings)
        # Fresh authorization is performed on submit too; a modal is not a grant.
        if developer is not None:
            await account_actor(self.bot, interaction.guild, interaction.user, developer.id)
        await interaction.response.send_modal(AccountModal(self.bot, developer.id if developer else None))

    @app_commands.command(name="account-remove", description="본인 계좌 삭제 또는 운영진의 개발자 계좌 대리 삭제")
    @app_commands.describe(developer="운영진 대리 삭제 대상 개발자 (생략하면 본인)")
    @app_commands.guild_only()
    async def remove_account(self, interaction: discord.Interaction, developer: discord.Member | None = None):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await remove_account(self.bot, interaction.guild, interaction.user, developer_id=developer.id if developer else None)
        await reply(interaction, "저장된 계좌를 삭제했습니다. 이미 티켓에 보낸 계좌 안내와 보관 기간 내 백업에는 남아 있을 수 있습니다.")

    @app_commands.command(name="account-send", description="담당 개발자 또는 운영진이 이 의뢰의 담당자 계좌 전송")
    @app_commands.guild_only()
    async def send_account(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await send_account(self.bot, interaction.guild, interaction.channel, interaction.user)
        await reply(interaction, "배정된 개발자의 계좌를 이 비공개 의뢰 티켓에 안내했습니다.")

    @app_commands.command(name="reviews", description="소유자가 커미션 후기 채널만 생성·카테고리 권한 동기화")
    @app_commands.describe(category="현재 사용 중인 커미션 카테고리 (생략하면 연결된 기존 카테고리)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def reviews(self, interaction: discord.Interaction, category: discord.CategoryChannel | None = None):
        self.owner(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            channel = await configure_reviews(self.bot, interaction.guild, category=category)
        await reply(interaction, f"{channel.mention}에 현재 커미션 카테고리 권한을 동기화했습니다. 납품 완료된 의뢰의 신청자는 후기 버튼으로 별점과 내용을 남길 수 있습니다.")

    @app_commands.command(name="review", description="납품 완료된 현재 의뢰의 신청자가 별점 후기 작성")
    @app_commands.guild_only()
    async def review(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        await open_review(self.bot, interaction)

    @app_commands.command(name="complete", description="담당 개발자·운영진이 납품 완료 처리하고 신청자에게 후기 요청")
    @app_commands.guild_only()
    async def complete(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await progress(self.bot, interaction.guild, interaction.channel, interaction.user, 'delivered')
        await reply(interaction, '납품 완료 처리했습니다. 신청자는 /ddh review로 후기를 작성할 수 있습니다. 후기 버튼 재전송은 /ddh review-request를 사용하세요.')

    @app_commands.command(name="review-request", description="담당 개발자·운영진이 완료된 의뢰의 후기 버튼을 티켓 아래로 전송")
    @app_commands.guild_only()
    async def review_request(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await request_review_by_command(self.bot, interaction.guild, interaction.channel, interaction.user)
        await reply(interaction, result)

    @app_commands.command(name="close", description="담당 개발자·운영진이 현재 의뢰를 종료하고 대화를 읽기 전용으로 보관")
    @app_commands.guild_only()
    async def close_commission(self, interaction: discord.Interaction):
        await open_commission_close(self.bot, interaction)

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

    @app_commands.command(name="updates", description="이번 업데이트 공지 자동 게시 켜기·끄기 (전체 멘션 없음)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def updates(self, interaction: discord.Interaction, enabled: bool):
        self.owner(interaction)
        if enabled and not release_announcements_allowed():
            raise UserError("소유자 요청으로 이 버전의 업데이트 공지·멘션 게시를 완전히 중지했습니다.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.update_lock:
            await self.bot.store.set(interaction.guild.id, RELEASE_NOTICES_KEY, enabled)
        await reply(interaction, (
            "이번 업데이트 자동 공지를 켰습니다. 아직 게시하지 않았다면 한 번 공지합니다. 전체 멘션은 없습니다."
            if enabled else "이번 업데이트 자동 공지를 껐습니다. 재시작 후에도 유지됩니다."
        ))

    @app_commands.command(name="status", description="Dialian 연결·구성 상태 확인")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        ready = await self.bot.store.get(interaction.guild.id, "setup_complete", False)
        notices = await self.bot.store.get(interaction.guild.id, OWNER_NOTICES_KEY, False)
        updates = release_announcements_allowed() and await self.bot.store.get(interaction.guild.id, RELEASE_NOTICES_KEY, True)
        security = await self.bot.store.get(interaction.guild.id, "security_complete", False)
        news_pending = await self.bot.store.get(interaction.guild.id, "news_format_pending", False)
        failure = None
        if not security and interaction.user.id == interaction.guild.owner_id:
            failure = await self.bot.store.get(interaction.guild.id, "security:last_error")
        details = f"\n최근 보안 오류: {failure}" if failure else ""
        if interaction.user.id == interaction.guild.owner_id:
            details += f"\n실행 코드: {(self.bot.release_sha or '확인 불가')[:12]}"
        if interaction.user.id == interaction.guild.owner_id:
            verification_error = await self.bot.store.get(
                interaction.guild.id, "verification:last_error",
            )
            if verification_error:
                details += f"\n최근 인증 오류: {verification_error}"
        await reply(interaction, f"Dialian · DDH v{__version__}\n서버 구성: {'완료' if ready else '미완료'}\n보안 설정: {'완료' if security else '미완료'}{details}\n"
                    f"공지형식: {'커뮤니티 활성화 필요' if news_pending else '설정 완료'}\n"
                    f"정기 안내: {'켜짐' if notices else '꺼짐'}\n이번 업데이트 공지: {'켜짐' if updates else '꺼짐'} · 전체 멘션 없음\n시작 시각: {self.bot.started_at}\n"
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

    async def setup_hook(self):
        if self.settings.database.exists():
            await backup(self.settings.database)
        await self.store.initialize()
        await backup(self.settings.database)
        self.last_backup_day = datetime.now(timezone.utc).date()
        for view in (RulesView(self), SupportView(self), TicketActions(self), CommissionActions(self), CommissionEntryView(self), ReviewRequestView(self), ReviewReceiptView(self)):
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
            await self.store.set(guild.id, "security_complete", False)
            try:
                await self.tickets.ready(guild)
                roles = await roles_for(guild, self.store, keys=("staff", "admin"))
                audit = await channel_for(guild, self.store, "security_log")
                if not private_channel(
                    audit, guild, {roles["staff"].id, roles["admin"].id, guild.me.id},
                ):
                    raise UserError(
                        "보안 설정 미완료 · 보안-security 채널의 비공개 권한 불일치. "
                        "서버 소유자는 /ddh operator로 이전 운영진 권한을 점검하고 "
                        "필요하면 /ddh operator apply:True로 적용해 주세요."
                    )
                safeguards = await configure_security(
                    guild, staff_role=roles["staff"], admin_role=roles["admin"],
                    audit_channel=audit,
                )
            except Exception as error:
                # Store a sanitized summary only; Discord tokens and response
                # bodies must not leak into public status or audit messages.
                detail = str(error) if isinstance(error, UserError) else (
                    f"내부 오류 ({type(error).__name__}) · 서비스 로그 확인"
                )
                await self.store.set(guild.id, "security:last_error", detail[:300])
                raise
            await self.store.set(guild.id, "security_complete", True)
            await self.store.set(guild.id, "security:last_error", None)
            return safeguards

    async def on_ready(self):
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("Configured DDH guild is unavailable; check DDH_GUILD_ID and bot invitation")
            return
        log.info("Dialian connected to DDH guild %s (owner-controlled configuration)", guild.id)
        # Apply only the owner's newly requested copy refresh once. There is
        # no general layout/permission/role restoration or repeated panel repair.
        if await self.store.get(guild.id, "setup_complete", False):
            try:
                await apply_requested_copy(self, guild)
            except Exception:
                log.exception("DDH requested copy pending; will retry")
            try:
                await publish_release(self, guild)
            except Exception:
                log.exception("DDH update announcement pending; will retry")

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
            # Finish only work a member or owner previously requested, such
            # as an interrupted ticket. Do NOT restore channels or policies.
            for name, action in (
                ("ticket recovery", self.tickets.recover),
                ("requested commission reviews", lambda g: recover_reviews(self, g)),
                ("owner-requested short copy", lambda g: apply_requested_copy(self, g)),
                ("owner-enabled scheduled notice", lambda g: publish_due(self, g)),
                ("owner-requested update announcement", lambda g: publish_release(self, g)),
            ):
                try:
                    await action(guild)
                except Exception:
                    log.exception("DDH %s failed; will retry", name)
        now = datetime.now(timezone.utc)
        if guild:
            try:
                await self.operations.catch_up(guild)
            except Exception:
                log.exception("DDH Discord audit catch-up failed; will retry")
            await self.operations.flush(guild)
        today = now.date()
        if self.last_backup_day != today:
            try:
                await backup(self.settings.database)
                if guild:
                    await self.store.purge_delivered_events(
                        guild.id, (now - timedelta(days=30)).isoformat()
                    )
                self.last_backup_day = today
            except Exception:
                log.exception("DDH backup failed; will retry")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.wait_until_ready()

    async def close(self):
        self.maintenance.cancel()
        await super().close()
