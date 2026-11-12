"""Verified SQLite snapshots; legacy DDS files are not modified."""
import asyncio
import os
import sqlite3
from shutil import copy2
from datetime import datetime, timezone
from pathlib import Path


def verify_snapshot(path):
    """Read-only recovery test. Do not touch the running DDH database."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"DDH backup corrupted: {result}")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"settings", "tickets", "operations_log"} <= tables:
            raise RuntimeError("DDH backup missing required tables")
        db.execute("SELECT COUNT(*) FROM tickets").fetchone()
        db.execute("SELECT COUNT(*) FROM settings").fetchone()
    return True


def latest_backup(source):
    directory = Path(source).parent / "ddh-backups"
    candidates = list(directory.glob("ddh-*.db"))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def restore_dry_run(source, destination):
    """Make a separate verified restore copy; never overwrite live data."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("복원 대상 파일이 이미 존재합니다. 덮어쓰지 않습니다.")
    verify_snapshot(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy2(source, destination)
        destination.chmod(0o600)
        verify_snapshot(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


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
        if os.name == "posix":
            target.chmod(0o600)
        verify_snapshot(target)
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
