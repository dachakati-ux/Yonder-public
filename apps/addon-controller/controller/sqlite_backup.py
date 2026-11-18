"""SQLite consistent online backup into an offline, version-bound bundle.

Uses sqlite3.Connection.backup to create a transactionally consistent SQLite
copy. It never mutates the source, runs migrations or accesses Docker.
Private backup bundles may still contain sensitive plaintext data: secure
host storage and independently verified encryption remain operator duties.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from pathlib import Path

from .backup_verifier import (
    BackupError,
    _MAX_TOTAL,
    create_offline_bundle,
)


def snapshot_sqlite_bundle(
    source_db: Path, destination: Path, *,
    addon_id: str, version: str, image: str,
) -> dict:
    """Create a consistent SQLite snapshot and verify copied backup bytes."""
    if source_db.is_symlink() or not source_db.is_file():
        raise BackupError("SQLite source must be a regular non-symlink file")
    info = source_db.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BackupError("SQLite source identity is untrusted")
    if info.st_size > _MAX_TOTAL:
        raise BackupError("SQLite source exceeds configured maximum")
    if destination.is_symlink() or destination.exists():
        raise BackupError("Backup destination must not exist")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise BackupError("Approved backup parent directory is missing")
    if os.name == "posix" and destination.parent.stat().st_mode & 0o022:
        raise BackupError("Backup parent must not be group/other writable")

    try:
        # A unique private directory keeps the consistent snapshot separate
        # from the mutable source until the offline bundle is sealed.
        with tempfile.TemporaryDirectory(
            prefix=".yonder-sqlite-snapshot-", dir=destination.parent
        ) as temporary:
            snapshot = Path(temporary) / "consistent.sqlite3"
            fd = os.open(
                snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(fd)
            # SQLite's native online backup API handles WAL/journal
            # consistency. A source database's business transactions still
            # require application-level validation after restoration.
            with sqlite3.connect(
                source_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=20
            ) as source:
                size = (
                    int(source.execute("PRAGMA page_count").fetchone()[0])
                    * int(source.execute("PRAGMA page_size").fetchone()[0])
                )
                if size > _MAX_TOTAL:
                    raise BackupError("SQLite page count exceeds backup limit")
                with sqlite3.connect(snapshot, timeout=20) as target:
                    source.backup(target, pages=128, sleep=0.2)
                    integrity = target.execute("PRAGMA quick_check").fetchone()
                    if integrity is None or integrity[0] != "ok":
                        raise BackupError("SQLite snapshot integrity check failed")
            return create_offline_bundle(
                snapshot, destination,
                addon_id=addon_id, version=version, image=image,
                backup_name="database.sqlite3", kind="database_export",
                # The completed *snapshot* is transactionally consistent and
                # no longer changes, even if the original source is live.
                source_quiesced_acknowledged=True,
            )
    except sqlite3.Error as exc:
        raise BackupError("SQLite consistent snapshot failed") from exc
