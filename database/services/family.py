"""DDS FAMILY: persistent 7-day trial and manually approved subscriptions."""
from datetime import datetime, timedelta, timezone
import aiosqlite
import discord
from config import BUYER_ROLE_ID, FAMILY_ROLE_ID, FAMILY_PROMOTION_CHANNEL_ID
from database.database import DATABASE

def utcnow():
    return datetime.now(timezone.utc)

async def init_family_tables():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS family_memberships (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
            PRIMARY KEY(guild_id,user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS family_trial_rollout (
            guild_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        await db.commit()

async def is_family_active(member):
    """A copied Discord role alone is not enough for financial benefits."""
    if not isinstance(member, discord.Member) or not any(r.id == FAMILY_ROLE_ID for r in member.roles):
        return False
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM family_memberships WHERE guild_id=? AND user_id=? AND state='active' AND expires_at>?",
            (member.guild.id, member.id, utcnow().isoformat()),
        ) as cur:
            return await cur.fetchone() is not None

def discount_rate(family, regular):
    return (30 if regular else 20) if family else (20 if regular else 0)

def discounted_price(base, family, regular):
    return base * (100-discount_rate(family,regular)) // 100

async def start_trial_once(guild):
    """Snapshot current buyers exactly once. Existing paid members are never downgraded."""
    await init_family_tables()
    role, buyer = guild.get_role(FAMILY_ROLE_ID), guild.get_role(BUYER_ROLE_ID)
    if not role or not buyer:
        print("[FAMILY] Missing FAMILY or buyer role; rollout postponed.")
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT 1 FROM family_trial_rollout WHERE guild_id=?", (guild.id,)) as cur:
            begun = await cur.fetchone()
        if not begun:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except (discord.HTTPException, discord.Forbidden) as exc:
                await db.rollback()
                print(f"[FAMILY] Unable to fetch complete buyer snapshot: {exc}")
                return
            start, expiry = utcnow(), utcnow()+timedelta(days=7)
            await db.execute("INSERT INTO family_trial_rollout VALUES(?,?,?)", (guild.id,start.isoformat(),expiry.isoformat()))
            for m in members:
                if not m.bot and any(r.id == BUYER_ROLE_ID for r in m.roles):
                    await db.execute(
                        "INSERT OR IGNORE INTO family_memberships VALUES(?,?,'trial',?,?,'active')",
                        (guild.id,m.id,start.isoformat(),expiry.isoformat()),
                    )
            await db.commit()
            print(f"[FAMILY] Trial snapshot saved; expiry={expiry.isoformat()}")
        else:
            await db.rollback()
    await reconcile_roles(guild)

async def reconcile_roles(guild):
    """DB expiry precedes role edits. Failed role edits retry on next run."""
    await init_family_tables()
    role=guild.get_role(FAMILY_ROLE_ID)
    if not role:
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE family_memberships SET state='expired' WHERE guild_id=? AND state='active' AND expires_at<=?",
                         (guild.id,utcnow().isoformat()))
        async with db.execute("SELECT user_id,state FROM family_memberships WHERE guild_id=?", (guild.id,)) as cur:
            rows=await cur.fetchall()
        await db.commit()
    for uid,state in rows:
        member=guild.get_member(uid)
        if member is None:
            try:
                member=await guild.fetch_member(uid)
            except (discord.HTTPException,discord.Forbidden):
                continue
        try:
            if state=="active" and role not in member.roles:
                await member.add_roles(role,reason="DDS FAMILY active membership")
            elif state!="active" and role in member.roles:
                await member.remove_roles(role,reason="DDS FAMILY expired membership")
        except (discord.HTTPException,discord.Forbidden) as exc:
            print(f"[FAMILY] Role sync failed for {uid}: {exc}")

async def activate_paid_after_confirmation(guild,member,days):
    """Human-admin action after payment verification. No recurring charging."""
    if not 1<=days<=366:
        raise ValueError("구독 기간은 1~366일이어야 합니다.")
    await init_family_tables()
    now=utcnow()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT expires_at FROM family_memberships WHERE guild_id=? AND user_id=? AND state='active' AND kind='paid'",
            (guild.id,member.id),
        ) as cur:
            row=await cur.fetchone()
        end=max(now,datetime.fromisoformat(row[0]) if row else now)+timedelta(days=days)
        await db.execute(
            """INSERT INTO family_memberships VALUES(?,?,'paid',?,?,'active')
            ON CONFLICT(guild_id,user_id) DO UPDATE SET
            kind='paid',starts_at=excluded.starts_at,expires_at=excluded.expires_at,state='active'""",
            (guild.id,member.id,now.isoformat(),end.isoformat()),
        )
        await db.commit()
    role=guild.get_role(FAMILY_ROLE_ID)
    if role is None:
        raise ValueError("FAMILY 역할이 존재하지 않습니다.")
    await member.add_roles(role,reason="DDS FAMILY payment verified by administrator")
    return end


async def ensure_family_promo_permissions(guild):
    """Restrict the existing promotion channel without altering staff overwrites."""
    channel = guild.get_channel(FAMILY_PROMOTION_CHANNEL_ID)
    role = guild.get_role(FAMILY_ROLE_ID)
    if not isinstance(channel, discord.TextChannel) or role is None:
        print("[FAMILY] Promotion channel/role missing; access setup postponed.")
        return
    try:
        everyone = channel.overwrites_for(guild.default_role)
        if everyone.view_channel is not False:
            everyone.view_channel = False
            await channel.set_permissions(guild.default_role, overwrite=everyone,
                                          reason="DDS FAMILY promotion private channel")
        family = channel.overwrites_for(role)
        if family.view_channel is not True or family.send_messages is not True:
            family.view_channel = True
            family.send_messages = True
            await channel.set_permissions(role, overwrite=family,
                                          reason="DDS FAMILY promotion benefit")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[FAMILY] Unable to set promotion access: {exc}")
