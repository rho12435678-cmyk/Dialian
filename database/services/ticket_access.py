"""One database-first authorization policy for ticket buttons and commands.

For known DB tickets the stored assignment is authoritative. Legacy tickets
without a DB row may fall back to a bot-authored application embed only.
"""
from dataclasses import dataclass
import re

import aiosqlite

from config import DESIGNER_ROLE_IDS
from database import database
from database.services.ticket_layout import ticket_kind

MENTION = re.compile(r"<@!?(\d+)>")


@dataclass(frozen=True)
class TicketAssignment:
    known: bool
    designer_id: int | None = None
    category: str | None = None
    status: str | None = None
    legacy: bool = False


async def ticket_assignment(channel, *, database_path=None):
    path = database_path or database.DATABASE
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT designer_id, category, status FROM commissions WHERE ticket_channel=?",
            (channel.id,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is not None:
        return TicketAssignment(
            known=True,
            designer_id=int(row[0]) if row[0] else None,
            category=row[1],
            status=row[2],
        )
    # Legacy-only fallback: never infer a responsible designer from a random
    # customer mention, description, unrelated embed, or bot-independent post.
    bot_id = getattr(getattr(channel.guild, "me", None), "id", None)
    if not bot_id:
        return TicketAssignment(known=False)
    async for message in channel.history(limit=40, oldest_first=True):
        if getattr(getattr(message, "author", None), "id", None) != bot_id:
            continue
        for embed in message.embeds:
            title = getattr(embed, "title", "") or ""
            if "신청서" not in title and "접수" not in title:
                continue
            for field in embed.fields:
                if field.name != "👨‍💻 담당 디자이너":
                    continue
                mention = MENTION.search(field.value or "")
                if mention:
                    return TicketAssignment(
                        known=True, designer_id=int(mention.group(1)),
                        legacy=True,
                    )
    return TicketAssignment(known=False)


def can_manage_assignment(member, assignment):
    if member is None:
        return False
    permissions = getattr(member, "guild_permissions", None)
    if permissions is not None and permissions.administrator:
        return True
    if not assignment.known or assignment.status in ("closed", "cancelled"):
        return False
    if assignment.designer_id is not None:
        return member.id == assignment.designer_id
    # A missing designer is NOT a permit for all designers. Scope unclaimed
    # tickets to the matching specialty, and keep support/partner tickets admin-only.
    kind = ticket_kind(assignment.category or "")
    role_id = DESIGNER_ROLE_IDS.get(kind)
    return bool(role_id and any(r.id == role_id for r in member.roles))


async def can_manage_channel(member, channel):
    return can_manage_assignment(member, await ticket_assignment(channel))
