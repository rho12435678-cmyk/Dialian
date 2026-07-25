from datetime import datetime
from pathlib import Path

import aiosqlite

from database.database import DATABASE


async def backup_database():
    source_path = Path(DATABASE)
    if not source_path.exists():
        return None
    backup_dir = Path("data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        backup_dir.chmod(0o700)
    except OSError:
        pass
    backup_path = backup_dir / f"dialian-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    async with aiosqlite.connect(DATABASE) as source:
        async with aiosqlite.connect(backup_path) as destination:
            await source.backup(destination)
    try:
        backup_path.chmod(0o600)
    except OSError:
        pass
    cutoff = datetime.now().timestamp() - (14 * 86400)
    for old_backup in backup_dir.glob("dialian-*.db"):
        if old_backup.stat().st_mtime < cutoff:
            old_backup.unlink()
    return backup_path
