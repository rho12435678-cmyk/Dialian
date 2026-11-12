"""Discord-only DDH release notifications.

Source control is used privately for detecting a new deployment. Public Discord
messages never include the repository owner, URL, commit hash, author, or
unreviewed Git commit subjects.
"""
import logging
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import discord

from ddh.server import channel_for, require_guild, roles_for

log = logging.getLogger(__name__)
REPOSITORY = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
NONCE = re.compile(r"[0-9a-f]{24}\Z")


def _git(*args):
    try:
        output = subprocess.run(
            ["git", "-C", str(REPOSITORY), *args],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return output.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def deployed_revision():
    """Private revision used solely to tell whether new code was deployed."""
    revision = _git("rev-parse", "--verify", "HEAD")
    return revision if SHA.fullmatch(revision) else None


def _public_notes(previous, revision):
    """Summarize changed components without displaying any Git metadata."""
    if previous and SHA.fullmatch(previous):
        changed = _git("diff", "--name-only", f"{previous}..{revision}")
    else:
        changed = _git("show", "--pretty=format:", "--name-only", revision)

    categories = []
    filenames = set(changed.splitlines())

    def include(condition, description):
        if condition and description not in categories:
            categories.append(description)

    include("ddh/updates.py" in filenames, "업데이트 공지 자동화 및 표시 방식 개선")
    include(
        bool(filenames & {"ddh/blueprint.py", "ddh/server.py", "ddh/layout.py"}),
        "서버 채널·역할·권한 구성 개선",
    )
    include(
        bool(filenames & {"ddh/security.py", "ddh/operations.py", "ddh/verification.py"}),
        "보안·인증 및 운영 관리 기능 개선",
    )
    include(
        bool(filenames & {"ddh/tickets.py", "ddh/views.py"}),
        "문의·신청 및 사용자 기능 개선",
    )
    include(
        bool(filenames & {"ddh/content.py", "ddh/notices.py"}),
        "안내 및 공지 내용·표시 개선",
    )
    include(
        bool(filenames & {"ddh/bot.py", "ddh/backups.py", "dial.py", "config.py"}),
        "봇 실행 및 안정성 개선",
    )
    if not categories:
        categories.append("봇 운영 및 유지보수 개선")

    # Optional private editorial notes are kept only on the VPS: data/ is
    # gitignored. Exact-revision filenames prevent stale notes from being
    # published again on later deployments.
    private_file = REPOSITORY / "data" / "release-notes" / (revision + ".txt")
    try:
        if private_file.is_file():
            private_lines = []
            for raw in private_file.read_text(encoding="utf-8").splitlines():
                text = raw.strip().lstrip("•- ").strip()
                # No account names, emails, mentions or links in public notes.
                if not text or re.search(r"(?i)(github|https?://|@)", text):
                    continue
                private_lines.append(discord.utils.escape_markdown(text[:160]))
                if len(private_lines) == 5:
                    break
            if private_lines:
                return "\n".join("• " + line for line in private_lines)[:1000]
    except (OSError, UnicodeError):
        log.exception("Private DDH release notes could not be read; using safe summary")

    return "\n".join("• " + value for value in categories[:5])


def release_embed(previous, revision, member_role=None, marker=None):
    """Build a polished announcement with no source account or revision leaks."""
    audience = f"🔔 **업데이트 안내 · {member_role.mention}**\n\n" if member_role else ""
    card = discord.Embed(
        title="🚀 DDH · 서버 업데이트 안내",
        description=(
            audience
            + "**Dial Dev Hub에 새로운 개선 사항이 적용되었습니다.**\n"
            + "더 편리하고 안전한 개발자 커뮤니티를 위한 업데이트입니다."
        ),
        colour=0xF39C12,
        timestamp=datetime.now(timezone.utc),
    )
    card.add_field(name="✨ 주요 변경사항", value=_public_notes(previous, revision), inline=False)
    card.add_field(name="✅ 적용 상태", value="DDH 서버에 적용 완료", inline=False)
    card.set_footer(text=marker or "Dial Dev Hub · 공식 업데이트")
    return card


async def publish_release(bot, guild):
    """Post one automatic member-ping update per deployed revision."""
    require_guild(guild, bot.settings)
    revision = bot.release_sha
    if not revision or not SHA.fullmatch(revision):
        return False
    if not await bot.store.get(guild.id, "setup_complete", False):
        return False

    async with bot.update_lock:
        previous = await bot.store.get(guild.id, "release:last_revision")
        if previous == revision:
            return False

        channel = await channel_for(guild, bot.store, "updates")
        pending = await bot.store.get(guild.id, "release:pending")
        if not isinstance(pending, dict) or pending.get("revision") != revision or not (
            isinstance(pending.get("nonce"), str) and NONCE.fullmatch(pending["nonce"])
        ):
            pending = {"revision": revision, "nonce": uuid.uuid4().hex[:24]}
            # Record before posting, so a retry can find the posted message.
            await bot.store.set(guild.id, "release:pending", pending)

        marker = "Dial Dev Hub · #" + pending["nonce"]
        old_marker = f"DDH:release:{revision}"
        async for message in channel.history(limit=500):
            if message.author.id == guild.me.id and any(
                item.footer.text in (marker, old_marker) for item in message.embeds
            ):
                await bot.store.set(guild.id, "release:last_revision", revision)
                return False

        member = (await roles_for(guild, bot.store, keys=("member",)))["member"]
        # A mention inside an embed does not create a Discord notification.
        if not channel.permissions_for(guild.me).mention_everyone:
            if not guild.me.guild_permissions.manage_channels:
                raise PermissionError(
                    "Dialian needs role-mention permission in the update channel"
                )
            overwrite = channel.overwrites_for(guild.me)
            overwrite.mention_everyone = True
            await channel.set_permissions(
                guild.me, overwrite=overwrite,
                reason="DDH 공식 공지: 봇만 멤버 역할 멘션 가능",
            )

        message = await channel.send(
            content=member.mention,
            embed=release_embed(previous, revision, member_role=member, marker=marker),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=[member],
            ),
        )
        await bot.store.set(guild.id, "release:last_revision", revision)
        await bot.store.set(guild.id, "release:last_message", message.id)
        log.info("DDH update announcement sent to channel %s", channel.id)
        return True
