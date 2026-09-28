"""Reconcile uncertain review publications and deliver pending credit safely."""
import re

import aiosqlite
import discord
from database import database
from database.services.ops_health import record_failure

FOOTER = re.compile(r"Ticket ID:\s*(\d+)")


def matching_review_posts(messages, bot_id, ticket_ids):
    """Pure, testable reconciliation: only official bot's review post qualifies."""
    found = {}
    for message in messages:
        if message.author.id != bot_id:
            continue
        for embed in message.embeds:
            if embed.title != "✨ 소중한 커미션 후기가 도착했습니다!":
                continue
            footer = getattr(embed.footer, "text", "") or ""
            match = FOOTER.search(footer)
            if match:
                ticket_id = int(match.group(1))
                if ticket_id in ticket_ids:
                    found[ticket_id] = message.id
    return found


async def reconcile_review_awards(
    guild, bot_user_id, review_channel, credit, refresh, *, path=None,
    history_limit=10000,
):
    """Never issue credit until a corresponding publication was confirmed."""
    path = path or database.DATABASE
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            """SELECT ticket_channel FROM review_point_awards
               WHERE status='unpublished' ORDER BY created_at LIMIT 30"""
        ) as cursor:
            unknown_ids = {r[0] for r in await cursor.fetchall()}

    published = {}
    if unknown_ids and review_channel:
        try:
            # Do not assume unscanned posts were never published.
            # Stream and stop as soon as every missing post is located.
            # Leave old unmatched reviews unpublished rather than issuing
            # unverified credit. The admin can inspect persistent alerts.
            async for message in review_channel.history(limit=history_limit):
                published.update(
                    matching_review_posts([message], bot_user_id, unknown_ids)
                )
                if len(published) == len(unknown_ids):
                    break
        except (discord.HTTPException, discord.Forbidden) as exc:
            await record_failure("review_repair", exc, path=path)

    if published:
        async with aiosqlite.connect(path) as db:
            await db.execute("BEGIN IMMEDIATE")
            for ticket_id, message_id in published.items():
                await db.execute(
                    """UPDATE review_point_awards
                       SET status='pending', review_message_id=?
                       WHERE ticket_channel=? AND status='unpublished'""",
                    (message_id, ticket_id),
                )
            await db.commit()

    async with aiosqlite.connect(path) as db:
        async with db.execute(
            """SELECT ticket_channel, customer_id FROM review_point_awards
               WHERE status='pending' ORDER BY created_at LIMIT 30"""
        ) as cursor:
            pending = await cursor.fetchall()
    for ticket_id, customer_id in pending:
        try:
            member = guild.get_member(customer_id) or discord.Object(id=customer_id)
            await credit(guild, member, ticket_id)
        except Exception as exc:
            await record_failure("review_repair", exc, path=path)
    try:
        await refresh(guild)
    except Exception as exc:
        await record_failure("review_repair", exc, path=path)
