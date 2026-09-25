import asyncio
import logging
import weakref

import discord
from discord.ext import commands

from config import CUSTOMER_ROLE_ID
from database.services.roblox_verification import (
    VerificationError,
    VerificationStore,
    fetch_eligible_profile,
    lookup_username,
)


logger = logging.getLogger(__name__)
store = VerificationStore()
_locks = weakref.WeakValueDictionary()
_cooldowns = commands.CooldownMapping.from_cooldown(
    1, 10, lambda interaction: (interaction.guild_id, interaction.user.id),
)


def member_lock(guild_id, user_id):
    key = (guild_id, user_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def check_cooldown(interaction):
    if _cooldowns.update_rate_limit(interaction):
        raise VerificationError("요청이 너무 빠릅니다. 10초 뒤에 다시 시도해주세요.")


def check_permissions(interaction):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        raise VerificationError("서버 안에서 인증해주세요.")
    # The guild owner can prove Roblox account ownership even though Discord
    # prevents bots from editing the server owner's nickname/roles.
    if interaction.user.id == guild.owner_id:
        return None
    me = guild.me
    if me is None or not me.guild_permissions.manage_nicknames:
        raise VerificationError("봇에 '별명 관리' 권한이 필요합니다. 서버 관리자에게 알려주세요.")
    if interaction.user.top_role >= me.top_role:
        raise VerificationError("봇 역할을 본인의 가장 높은 역할보다 위로 옮겨야 닉네임을 바꿀 수 있습니다.")
    role = guild.get_role(CUSTOMER_ROLE_ID)
    if role is None or role.is_default() or role.managed:
        raise VerificationError("인증 역할 설정을 확인해주세요. config.py의 CUSTOMER_ROLE_ID를 확인하세요.")
    if not me.guild_permissions.manage_roles or role >= me.top_role:
        raise VerificationError("봇에 '역할 관리' 권한을 주고, 봇 역할을 인증 역할보다 위에 놓아주세요.")
    return role


async def apply_verified_profile(interaction, profile, *, updated=False):
    role = check_permissions(interaction)
    if interaction.user.id == interaction.guild.owner_id:
        # Roblox proof and account-age rules are checked by VerificationStore.
        # Do not attempt forbidden nickname/role edits on the guild owner.
        return await interaction.followup.send(
            f"✅ Roblox {'인증 정보 업데이트' if updated else '인증'} 완료! "
            f"연결된 계정: **{discord.utils.escape_markdown(profile.name)}**\n"
            "서버 소유자는 Discord 제한으로 봇이 닉네임과 역할을 변경하지 않습니다. "
            "원하면 닉네임을 직접 변경해 주세요."
            + ("\nRoblox 소개란의 인증 코드는 이제 지워도 됩니다." if not updated else ""),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    try:
        await interaction.user.edit(nick=profile.name, reason="Roblox account verified")
        if role not in interaction.user.roles:
            await interaction.user.add_roles(role, reason="Roblox account verified")
    except discord.Forbidden as exc:
        raise VerificationError(
            "계정 인증은 저장했지만 닉네임 또는 역할 적용이 거부되었습니다. "
            "봇 권한과 역할 순서를 수정한 뒤 '인증 정보 업데이트'를 눌러주세요."
        ) from exc
    except discord.HTTPException as exc:
        raise VerificationError(
            "계정 인증은 저장했지만 디스코드 적용에 실패했습니다. 잠시 뒤 '인증 정보 업데이트'를 눌러주세요."
        ) from exc
    await interaction.followup.send(
        f"{'업데이트' if updated else '인증'} 완료! "
        f"서버 닉네임을 **{discord.utils.escape_markdown(profile.name)}**으로 설정했습니다.\n"
        + ("계정 생성일을 다시 확인했습니다." if updated
           else "로블록스 소개란의 인증 코드는 이제 지워도 됩니다."),
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )

async def report_error(interaction, error):
    if isinstance(error, VerificationError):
        message = str(error)
    else:
        logger.error("Roblox verification failed", exc_info=(type(error), error, error.__traceback__))
        message = "⚠️ 인증 중 오류가 발생했어요. 잠시 후 다시 시도하고, 반복되면 운영진에게 알려주세요."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class RobloxUsernameModal(discord.ui.Modal, title="로블록스 계정 인증"):
    username = discord.ui.TextInput(
        label="Roblox 사용자이름 (표시 이름 X)", placeholder="@사용자이름 (예: Roblox)",
        min_length=1, max_length=21,
    )

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        check_cooldown(interaction)
        async with member_lock(interaction.guild_id, interaction.user.id):
            check_permissions(interaction)
            profile = await lookup_username(self.username.value)
            link = await store.get_link(interaction.guild_id, interaction.user.id)
            if link and link[0] == profile.id:
                profile = await store.refresh(interaction.guild_id, interaction.user.id)
                return await apply_verified_profile(interaction, profile, updated=True)
            profile = await fetch_eligible_profile(profile.id)
            code = await store.issue(interaction.guild_id, interaction.user.id, profile)
            embed = discord.Embed(
                title="로블록스 계정 소유 확인", color=discord.Color.blurple(),
                description=(
                    f"대상 계정: **{discord.utils.escape_markdown(profile.name)}**\n\n"
                    "① 코드를 복사해 Roblox 프로필의 **소개(About)**에 붙여넣고 저장하세요.\n"
                    "② 아래 **인증 확인** 버튼을 눌러주세요.\n\n"
                    f"```\n{code}\n```\n⏳ 코드 유효 시간: 10분 · 비밀번호는 필요 없어요."
                ),
            )
            view = RobloxConfirmView()
            view.add_item(discord.ui.Button(label="로블록스 프로필", url=f"https://www.roblox.com/users/{profile.id}/profile"))
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class VerificationViewBase(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


class RobloxConfirmView(VerificationViewBase):
    @discord.ui.button(label="인증 확인", style=discord.ButtonStyle.success, custom_id="roblox_verify_confirm")
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        check_cooldown(interaction)
        async with member_lock(interaction.guild_id, interaction.user.id):
            check_permissions(interaction)
            profile = await store.verify(interaction.guild_id, interaction.user.id)
            await apply_verified_profile(interaction, profile)


class VerifyView(VerificationViewBase):
    @discord.ui.button(label="로블록스 인증하기", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify(self, interaction, button):
        check_permissions(interaction)
        await interaction.response.send_modal(RobloxUsernameModal())

    @discord.ui.button(label="인증 정보 업데이트", style=discord.ButtonStyle.secondary, custom_id="roblox_verify_sync")
    async def sync(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        check_cooldown(interaction)
        async with member_lock(interaction.guild_id, interaction.user.id):
            check_permissions(interaction)
            link = await store.get_link(interaction.guild_id, interaction.user.id)
            if not link:
                return await interaction.followup.send(
                    "기존 서버 회원도 최초 1회 로블록스 계정 연결이 필요합니다. "
                    "아래 '로블록스 인증하기'를 눌러주세요.",
                    view=VerifyView(), ephemeral=True,
                )
            profile = await store.refresh(interaction.guild_id, interaction.user.id)
            await apply_verified_profile(interaction, profile, updated=True)
