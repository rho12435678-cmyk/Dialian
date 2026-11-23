"""Award the cosmetic client role once per verified commission reviewer."""
import logging
import time

import discord

from ddh.server import UserError, find_resource

log = logging.getLogger(__name__)


async def client_role(bot, guild):
    """Caller holds mutation_lock. Preserve owner edits and role identity."""
    role_id = await bot.store.get(guild.id, 'role:client')
    if role_id:
        role = guild.get_role(role_id)
        if role is None:
            raise UserError('등록된 의뢰자 역할을 찾을 수 없습니다.')
    else:
        role = await find_resource(guild, bot.store, 'role:client', '의뢰자', discord.Role)
        if role is None:
            role = await guild.create_role(name='의뢰자', permissions=discord.Permissions.none(),
                                           reason='DDH 별점 후기 작성자용 역할')
    if (role.is_default() or role.managed or role.permissions.value or role >= guild.me.top_role
            or not guild.me.guild_permissions.manage_roles):
        raise UserError('의뢰자 역할은 권한이 없는 일반 역할이어야 하며 Dialian이 지급할 수 있어야 합니다.')
    member_role = guild.get_role(await bot.store.get(guild.id, 'role:member', 0))
    for channel in guild.channels:
        allow, _ = channel.overwrites_for(role).pair()
        if not allow.value:
            continue
        # Owner-run provisioning puts public read-only overrides on every
        # role. Those are harmless where @everyone can already view history.
        public = channel.permissions_for(guild.default_role)
        if public.view_channel:
            allow.view_channel = False
        if public.read_message_history or (member_role and channel.permissions_for(member_role).read_message_history):
            allow.read_message_history = False
        if allow.value:
            raise UserError('의뢰자 역할에 별도 채널 접근 또는 작업 권한이 있습니다.')
    if role_id != role.id:
        await bot.store.set(guild.id, 'role:client', role.id)
    return role


async def grant_client(bot, guild, owner_id):
    """A saved review queues the award even if the ticket is later deleted."""
    retry_at = int(time.time()) + 120
    try:
        member = await guild.fetch_member(owner_id)
        verified = await bot.store.get(guild.id, 'role:member')
        if member.bot or member.pending or not any(r.id == verified for r in member.roles):
            await bot.store.client_result(guild.id, owner_id, retry_at=retry_at + 3600)
            return
        role = await client_role(bot, guild)
        if role not in member.roles:
            await member.add_roles(role, reason='DDH 납품 완료 의뢰의 신청자가 별점 후기 작성')
    except discord.NotFound:
        await bot.store.client_result(guild.id, owner_id, retry_at=retry_at + 86400)
    except (discord.HTTPException, UserError) as error:
        log.warning('DDH client role grant pending: %s', error)
        await bot.store.client_result(guild.id, owner_id, retry_at=retry_at)
    else:
        await bot.store.client_result(guild.id, owner_id, retry_at=None)
