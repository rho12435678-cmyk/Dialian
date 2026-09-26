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
    """Treat a staff-assigned FAMILY role as a trial entitlement during rollout.

    A lost DB record is automatically restored with the ORIGINAL global
    rollout expiry. Existing paid, cancelled and expired rows are authoritative:
    none is silently overwritten or extended. Role alone never bypasses
    the end of the published 7-day trial.
    """
    if not isinstance(member, discord.Member):
        return False
    if not any(r.id == FAMILY_ROLE_ID for r in member.roles):
        return False
    await init_family_tables()
    now = utcnow()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            """SELECT state, expires_at FROM family_memberships
               WHERE guild_id=? AND user_id=?""",
            (member.guild.id, member.id),
        ) as cur:
            record = await cur.fetchone()
        if record is not None:
            return record[0] == "active" and datetime.fromisoformat(record[1]) > now

        async with db.execute(
            "SELECT started_at, expires_at FROM family_trial_rollout WHERE guild_id=?",
            (member.guild.id,),
        ) as cur:
            rollout = await cur.fetchone()
        if not rollout:
            return False
        started, expires = rollout
        if not (datetime.fromisoformat(started) <= now < datetime.fromisoformat(expires)):
            return False
        # The role is already deliberately granted by staff. Repair its lost
        # personal record in-place, without restarting anybody's trial.
        await db.execute(
            """INSERT OR IGNORE INTO family_memberships
               (guild_id,user_id,kind,starts_at,expires_at,state)
               VALUES (?,?,'trial',?,?,'active')""",
            (member.guild.id, member.id, started, expires),
        )
        await db.commit()
        async with db.execute(
            """SELECT state, expires_at FROM family_memberships
               WHERE guild_id=? AND user_id=?""",
            (member.guild.id, member.id),
        ) as cur:
            repaired = await cur.fetchone()
        return bool(
            repaired and repaired[0] == "active"
            and datetime.fromisoformat(repaired[1]) > now
        )

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
    """Synchronize FAMILY role holders, including roles granted manually.

    During the original 7-day rollout, reconcile any staff-granted role that
    missed the snapshot. Once it expires, revoke both recorded and orphaned
    trial roles while preserving every active paid membership.
    """
    await init_family_tables()
    role = guild.get_role(FAMILY_ROLE_ID)
    if role is None:
        return
    now = utcnow()
    role_members = [m for m in role.members if not m.bot]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT started_at, expires_at FROM family_trial_rollout WHERE guild_id=?",
            (guild.id,),
        ) as cur:
            rollout = await cur.fetchone()
        if rollout:
            started, expires = rollout
            if datetime.fromisoformat(started) <= now < datetime.fromisoformat(expires):
                for member in role_members:
                    await db.execute(
                        """INSERT OR IGNORE INTO family_memberships
                           (guild_id,user_id,kind,starts_at,expires_at,state)
                           VALUES (?,?,'trial',?,?,'active')""",
                        (guild.id, member.id, started, expires),
                    )
        await db.execute(
            """UPDATE family_memberships SET state='expired'
               WHERE guild_id=? AND state='active' AND expires_at<=?""",
            (guild.id, now.isoformat()),
        )
        async with db.execute(
            "SELECT user_id, state, expires_at FROM family_memberships WHERE guild_id=?",
            (guild.id,),
        ) as cur:
            records = await cur.fetchall()
        await db.commit()

    active_ids = {
        uid for uid, state, expires_at in records
        if state == "active" and datetime.fromisoformat(expires_at) > now
    }
    for uid, state, _ in records:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        try:
            if uid in active_ids and role not in member.roles:
                await member.add_roles(role, reason="DDS FAMILY active membership")
            elif uid not in active_ids and role in member.roles:
                await member.remove_roles(role, reason="DDS FAMILY expired membership")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[FAMILY] Role sync failed for {uid}: {exc}")

    # A manually added role may still have no DB entry when the trial is over.
    # Revoke these orphan roles too; otherwise they could keep access to the
    # role-permission promotion channel after the expiry deadline.
    if rollout and datetime.fromisoformat(rollout[1]) <= now:
        for member in role_members:
            if member.id not in active_ids and role in member.roles:
                try:
                    await member.remove_roles(
                        role, reason="DDS FAMILY free trial expired (orphan role)"
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[FAMILY] Orphan role removal failed user={member.id}: {exc}")


async def reconcile_admin_confirmed_trial(guild, member):
    """Repair an existing-buyer trial role manually granted by staff.

    Only administrators should call this after verifying the buyer held the
    role at the original rollout. Reuses the existing global trial dates,
    never extends an expired trial or changes an existing paid membership.
    """
    await init_family_tables()
    ids = {role.id for role in member.roles}
    if BUYER_ROLE_ID not in ids or FAMILY_ROLE_ID not in ids:
        raise ValueError("구매자 역할과 FAMILY 역할을 모두 보유해야 합니다.")
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT started_at, expires_at FROM family_trial_rollout WHERE guild_id=?",
            (guild.id,),
        ) as cur:
            dates = await cur.fetchone()
        if not dates:
            await db.rollback()
            raise ValueError("이 서버의 무료 체험 시작 기록이 없습니다.")
        started, expires = dates
        if datetime.fromisoformat(expires) <= utcnow():
            await db.rollback()
            raise ValueError("원래 무료 체험 기간이 이미 종료되어 연장할 수 없습니다.")
        cursor = await db.execute(
            """INSERT OR IGNORE INTO family_memberships
               (guild_id,user_id,kind,starts_at,expires_at,state)
               VALUES (?,?,'trial',?,?,'active')""",
            (guild.id, member.id, started, expires),
        )
        inserted = cursor.rowcount == 1
        await db.commit()
    return inserted, expires

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
