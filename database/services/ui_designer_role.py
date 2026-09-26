"""Create or discover the DDS UI designer role and reuse its stable Discord ID.

Permissionless on the new role: staff explicitly assign vetted designers.
Never grant elevated guild permissions or assign designers automatically.
"""
import asyncio
import logging

import aiosqlite
import discord

from config import DESIGNER_ROLE_IDS
from database.database import DATABASE

log = logging.getLogger(__name__)
_lock = asyncio.Lock()
UI_DESIGNER_ROLE_NAME = "🖥️ UI 디자이너"


async def ensure_ui_designer_role(guild):
    """Idempotent role registration across bot restarts and role renames."""
    if guild is None:
        return None
    async with _lock:
        setting_key = f"ui_designer_role_id:{guild.id}"
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT value FROM bot_settings WHERE key=?", (setting_key,)
            ) as cursor:
                recorded = await cursor.fetchone()
        role = guild.get_role(int(recorded[0])) if recorded else None
        if role is None:
            role = discord.utils.get(guild.roles, name=UI_DESIGNER_ROLE_NAME)
        if role is None:
            me = guild.me
            if me is None or not me.guild_permissions.manage_roles:
                log.warning("UI designer role is absent; give Dialian Manage Roles.")
                return None
            try:
                role = await guild.create_role(
                    name=UI_DESIGNER_ROLE_NAME,
                    permissions=discord.Permissions.none(),
                    hoist=False,
                    mentionable=False,
                    reason="DDS Roblox UI preview designer role",
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("UI designer role creation failed: %s", exc)
                return None

        # All existing role checks share this mutable mapping.
        DESIGNER_ROLE_IDS["ui"] = role.id
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """INSERT INTO bot_settings (key,value) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (setting_key, str(role.id)),
            )
            await db.commit()
        return role
