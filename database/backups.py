"""Create restorable SQLite backups and verify them before retention cleanup."""
import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile

import aiosqlite

from database.database import DATABASE


def verify_backup_restore(backup_path):
    """Restore into a separate temporary database; never touch the live DB."""
    backup_path = Path(backup_path)
    with tempfile.TemporaryDirectory(prefix="dialian-restore-check-") as tmp:
        restored = Path(tmp) / "restored.db"
        with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as source:
            with sqlite3.connect(restored) as target:
                source.backup(target)
        with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError("SQLite backup restore integrity check failed")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required = {"commissions", "reviews", "review_point_awards", "user_points"}
            if not required.issubset(tables):
                raise RuntimeError("Restored backup is missing critical tables")
            # Reading every operational table verifies the restored schema
            # without exposing customer records to logs.
            for name in sorted(required):
                conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
    return True


async def backup_database(*, database_path=None, backup_dir=None):
    source_path = Path(database_path or DATABASE)
    if not source_path.exists():
        return None
    directory = Path(backup_dir or "data/backups")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    path = directory / f"dialian-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
    try:
        async with aiosqlite.connect(source_path) as source:
            async with aiosqlite.connect(path) as destination:
                await source.backup(destination)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        await asyncio.to_thread(verify_backup_restore, path)
    except Exception:
        # A broken snapshot must not be considered a usable backup.
        path.unlink(missing_ok=True)
        raise
    # Delete old snapshots only AFTER a restorable new backup exists.
    cutoff = datetime.now().timestamp() - 14 * 86400
    for old in directory.glob("dialian-*.db"):
        if old != path and old.stat().st_mtime < cutoff:
            old.unlink()
    return path
