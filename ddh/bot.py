import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from ddh.backups import backup
from ddh.blueprint import layout_summary
from ddh.content import publish_panels
from ddh.notices import publish_due
from ddh.server import UserError, is_admin, preflight, provision, require_guild
from ddh.store import Store, utcnow
from ddh.tickets import Tickets
from ddh.views import RulesView, SupportView, TicketActions, reply, report_error

log = logging.getLogger(__name__)


class DDHCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="ddh", description="Dial Dev Hub 서버 관리·안내")
        self.bot = bot

    def admin(self, interaction):
        require_guild(interaction.guild, self.bot.settings)
        if not is_admin(interaction.user):
            raise UserError("서버 소유자 또는 관리자만 사용할 수 있습니다.")

    @app_commands.command(name="plan", description="DDH 채널·역할·권한 구성 미리보기")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def plan(self, interaction: discord.Interaction):
        self.admin(interaction)
        await reply(interaction, layout_summary())

    @app_commands.command(name="setup", description="DDH 서버 구성. apply=True일 때 실제 적용합니다")
    @app_commands.describe(apply="True: 역할·채널·권한과 안내 패널을 실제 생성/갱신")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, apply: bool = False):
        self.admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not apply:
            found = await preflight(interaction.guild, self.bot.store)
            await reply(interaction, layout_summary() + f"\n\n재사용 {sum(v is not None for v in found.values())}개 / "
                        f"새로 생성 {sum(v is None for v in found.values())}개. 적용: `/ddh setup apply:True`")
            return
        if self.bot.mutation_lock.locked():
            raise UserError("다른 서버 구성이 진행 중입니다. 잠시 후 다시 시도해 주세요.")
        async with self.bot.mutation_lock:
            roles, categories, channels = await provision(interaction.guild, self.bot.store, self.bot.settings)
            await publish_panels(self.bot, interaction.guild)
            if not await self.bot.store.get(interaction.guild.id, "setup_at"):
                await self.bot.store.set(interaction.guild.id, "setup_at", utcnow())
            await self.bot.store.set(interaction.guild.id, "setup_complete", True)
        await reply(interaction, f"DDH 구성 완료: 역할 {roles}개 · 카테고리 {categories}개 · 채널 {channels}개.\n"
                    "시작·규칙과 문의·신청 패널을 확인해 주세요. 운영진 역할은 서버 소유자가 직접 지정합니다.")

    @app_commands.command(name="panels", description="규칙·안내·문의 패널 복구 또는 갱신")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def panels(self, interaction: discord.Interaction):
        self.admin(interaction)
        await self.bot.tickets.ready(interaction.guild)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.mutation_lock:
            await publish_panels(self.bot, interaction.guild)
        await reply(interaction, "DDH 안내 패널을 갱신했습니다.")

    @app_commands.command(name="notices", description="정기 DDH 안내 공지 켜기/끄기")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def notices(self, interaction: discord.Interaction, enabled: bool):
        self.admin(interaction)
        await self.bot.store.set(interaction.guild.id, "notices_enabled", enabled)
        await reply(interaction, f"자동 공지 {'켜짐' if enabled else '꺼짐'} · "
                    f"한국 시간 {self.bot.settings.notice_hour}시 / {self.bot.settings.notice_interval_days}일 간격")

    @app_commands.command(name="status", description="Dialian 연결·구성 상태 확인")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        require_guild(interaction.guild, self.bot.settings)
        ready = await self.bot.store.get(interaction.guild.id, "setup_complete", False)
        notices = await self.bot.store.get(interaction.guild.id, "notices_enabled", self.bot.settings.notices_enabled)
        await reply(interaction, f"Dialian · DDH\n서버 구성: {'완료' if ready else '미완료'}\n"
                    f"자동 공지: {'켜짐' if notices else '꺼짐'}\n시작 시각: {self.bot.started_at}\n"
                    "문의는 문의·신청 패널을 이용해 주세요.")


class Dialian(discord.Client):
    def __init__(self, settings):
        intents = discord.Intents.none()
        intents.guilds = True
        # Slash commands and REST member lookups need no privileged intents.
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database)
        self.mutation_lock = asyncio.Lock()
        self.tickets = Tickets(self)
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.command_error
        self.started_at = utcnow()
        self.last_backup_day = None

    async def setup_hook(self):
        await self.store.initialize()
        await backup(self.settings.database)
        self.last_backup_day = datetime.now(timezone.utc).date()
        for view in (RulesView(self), SupportView(self), TicketActions(self)):
            self.add_view(view)
        target = discord.Object(id=self.settings.guild_id)
        self.tree.add_command(DDHCommands(self), guild=target)
        await self.tree.sync(guild=target)
        self.maintenance.start()

    async def on_ready(self):
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("Configured DDH guild is unavailable; check DDH_GUILD_ID and bot invitation")
        else:
            log.info("Dialian connected to DDH guild %s; no automatic server provisioning", guild.id)

    async def on_guild_channel_delete(self, channel):
        if channel.guild.id != self.settings.guild_id:
            return
        async with self.mutation_lock:
            ticket = await self.store.channel_ticket(channel.guild.id, channel.id)
            if ticket:
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
        today = datetime.now(timezone.utc).date()
        if self.last_backup_day != today:
            try:
                await backup(self.settings.database)
                self.last_backup_day = today
            except Exception:
                log.exception("DDH backup failed; will retry")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.wait_until_ready()

    async def close(self):
        self.maintenance.cancel()
        await super().close()
