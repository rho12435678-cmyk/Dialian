"""Verified SQLite snapshots; legacy DDS files are not modified."""
import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def snapshot(source):
    source = Path(source)
    directory = source.parent / "ddh-backups"
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        directory.chmod(0o700)
    target = directory / f"ddh-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.db"
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("DDH backup integrity check failed")
            tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"settings", "tickets"} <= tables:
                raise RuntimeError("DDH backup schema check failed")
        if os.name == "posix":
            target.chmod(0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    cutoff = datetime.now(timezone.utc).timestamp() - 14 * 86400
    for old in directory.glob("ddh-*.db"):
        if old != target and old.stat().st_mtime < cutoff:
            old.unlink()
    return target


async def backup(path):
    return await asyncio.to_thread(snapshot, path)
