"""Publish the DDS UI commission poll and its customer-role reply exactly once.

Uses the existing bot_settings database and Discord history to recover from restarts
or a crash between Discord sending a message and recording its ID.
"""
import asyncio
import logging
from datetime import timedelta

import aiosqlite
import discord

from config import CUSTOMER_ROLE_ID
from database.database import DATABASE

log = logging.getLogger(__name__)
_lock = asyncio.Lock()

GUILD_ID = 1505074222161068136
CHANNEL_ID = 1505104390103765153
POLL_KEY = "dds_ui_commission_poll_2026_09_v1"
REPLY_KEY = "dds_ui_commission_poll_reply_2026_09_v1"

QUESTION = "GFX·복장 외에 UI 커미션 분야도 추가한다면 주문할 의향이 있으신가요?"
ANSWERS = ("무조건 있다", "비용 여건이 된다면 있다", "없다")
REPLY = (
    f"<@&{CUSTOMER_ROLE_ID}>\n\n"
    "투표 결과와 관계없이 UI 분야가 즉시 추가되지는 않습니다. "
    "디자이너분들과 주요 사항을 충분히 논의한 후, 최종 승인 시 도입할 예정입니다."
)


async def _get(key):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else None


async def _save(key, message_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO bot_settings(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, str(message_id)),
        )
        await db.commit()


def _is_ui_poll(message):
    poll = getattr(message, "poll", None)
    question = getattr(poll, "question", "") if poll else ""
    # Only reuse our own poll or the known earlier DDS poll question.
    # Generic UI-related polls must not receive this announcement.
    return isinstance(question, str) and (
        question == QUESTION
        or ("UI" in question.upper() and "주문제작 맡길 의향" in question)
    )


async def publish_ui_poll_once(bot):
    """Reuse an existing UI poll if present; otherwise publish one and reply once."""
    async with _lock:
        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise RuntimeError("DDS 투표 채널/서버를 확인할 수 없습니다.")
        if bot.user is None:
            raise RuntimeError("봇 계정이 아직 준비되지 않았습니다.")

        poll_id = await _get(POLL_KEY)
        poll_msg = None
        if poll_id:
            # Never repost if a past run was recorded, even if that poll was deleted.
            try:
                poll_msg = await channel.fetch_message(poll_id)
            except discord.NotFound:
                log.warning("Recorded UI poll %s was deleted; not reposting", poll_id)
                return False

        # History access is mandatory before any new send to prevent duplicates
        # after a crash before the DB commit. Reuse an existing manual poll too.
        seen_reply = None
        history = [m async for m in channel.history(limit=500)]
        if poll_msg is None:
            poll_msg = next((m for m in history if _is_ui_poll(m)), None)
            if poll_msg:
                await _save(POLL_KEY, poll_msg.id)

        if poll_msg is None:
            poll = discord.Poll(question=QUESTION, duration=timedelta(days=7))
            for answer in ANSWERS:
                poll.add_answer(text=answer)
            poll_msg = await channel.send(
                poll=poll,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await _save(POLL_KEY, poll_msg.id)
            log.info("DDS UI poll sent: %s", poll_msg.id)

        reply_id = await _get(REPLY_KEY)
        if reply_id:
            log.info("DDS UI poll reply already recorded: %s", reply_id)
            return False

        # Recover from a successful Discord reply followed by a failed DB write.
        for m in history:
            if (
                m.author.id == bot.user.id
                and m.reference
                and m.reference.message_id == poll_msg.id
                and "DDS UI 커미션 분야 도입 관련 안내" in m.content
            ):
                seen_reply = m
                break
        if seen_reply:
            await _save(REPLY_KEY, seen_reply.id)
            return False

        reply = await poll_msg.reply(
            REPLY,
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=CUSTOMER_ROLE_ID)],
                everyone=False,
                users=False,
                replied_user=False,
            ),
        )
        await _save(REPLY_KEY, reply.id)
        log.info("DDS UI poll reply posted: %s", reply.id)
        return True
