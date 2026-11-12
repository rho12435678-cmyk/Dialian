"""Owner-confirmed, destructive removal of old DDH ticket/archive categories.

Discord message history is NOT included in the SQLite backup. Every deletion
requires an explicit slash-command confirmation from the server owner.
"""
import discord

from ddh.backups import backup
from ddh.blueprint import overwrites
from ddh.server import UserError, roles_for

LEGACY_CATEGORIES = {
    "tickets": ("🔐 티켓・Tickets", "🔒 문의 진행"),
    "archive": ("📁 보관・Archive", "📁 문의 보관"),
}


async def targeted_categories(guild, store):
    result = []
    for key, names in LEGACY_CATEGORIES.items():
        saved_id = await store.get(guild.id, f"category:{key}", 0)
        matched = [
            cat for cat in guild.categories
            if cat.name in names or (saved_id and cat.id == saved_id)
        ]
        for cat in matched:
            if cat.id not in {item.id for item in result}:
                result.append(cat)
        # Include old ticket overflow categories created by Dialian.
        for category in guild.categories:
            if any(category.name.startswith(name + " ") for name in names):
                if category.id not in {item.id for item in result}:
                    result.append(category)
    return result


async def cleanup_legacy(guild, store, settings, *, confirm=False):
    """Remove only known DDH legacy category contents and categories.

    Current open/creating/closing ticket channels are MOVED into the private
    Staff section, not deleted, so users do not lose active applications.
    All other archive posts, closed tickets, and messages will be deleted.
    """
    categories = await targeted_categories(guild, store)
    channel_count = len({channel.id for cat in categories for channel in cat.channels})
    if not confirm:
        return (
            f"삭제 대상: 티켓·보관 카테고리 {len(categories)}개, "
            f"그 안의 채널 {channel_count}개.\n"
            "⚠️ 채널을 삭제하면 Discord 메시지·포럼 게시글과 첨부 파일은 "
            "복구할 수 없습니다. SQLite 백업에는 채팅 내용이 포함되지 않습니다.\n"
            "진행 중인 티켓은 운영 카테고리로 이동해 유지합니다. "
            "전체 삭제에 동의하면 서버 소유자가 "
            "`/ddh cleanup confirm:True`를 실행하세요."
        )

    staff_category = guild.get_channel(await store.get(guild.id, "category:ops", 0))
    if not isinstance(staff_category, discord.CategoryChannel):
        raise UserError("비공개 운영 카테고리를 먼저 구성해 주세요.")
    if not categories:
        return "정리할 기존 티켓·보관 카테고리가 없습니다."

    # Only the DB is backed up; Discord messages cannot be restored.
    await backup(settings.database)
    roles = await roles_for(guild, store)
    active = {}
    for state in ("creating", "open", "closing"):
        for ticket in await store.tickets(guild.id, state):
            if ticket["channel_id"]:
                active[ticket["channel_id"]] = ticket

    removed_channels = 0
    moved_tickets = 0
    removed_categories = 0
    for category in categories:
        for channel in list(category.channels):
            if channel.id in active:
                ticket = active[channel.id]
                try:
                    owner = await guild.fetch_member(ticket["owner_id"])
                except discord.NotFound:
                    owner = None
                private_policy = overwrites(
                    guild, roles, "private", owner=owner,
                    closed=(ticket["state"] == "closing"),
                )
                await channel.edit(
                    category=staff_category,
                    overwrites=private_policy,
                    reason="DDH 진행 중 문의 채널 보존",
                )
                moved_tickets += 1
            else:
                await channel.delete(reason="서버 소유자 확인: 이전 티켓·보관 채널 영구 정리")
                removed_channels += 1
        if not category.channels:
            await category.delete(reason="DDH 이전 티켓·보관 카테고리 정리")
            removed_categories += 1

    return (
        f"정리 완료: 채널 {removed_channels}개 삭제, "
        f"진행 중 티켓 {moved_tickets}개 비공개 이동, "
        f"카테고리 {removed_categories}개 삭제.\n"
        "삭제된 Discord 메시지는 복구할 수 없습니다."
    )
