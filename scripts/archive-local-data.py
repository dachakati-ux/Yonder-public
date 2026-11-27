"""Archive a quiesced local Yonder data volume to stdout.

Run only after all writers have stopped. SQLite files are consolidated using
the backup API (including committed WAL contents), checked, and archived
without stale journal sidecars. No archive is emitted on validation failure.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path
from typing import BinaryIO


def archive_data(root: Path, output: BinaryIO) -> None:
    if not root.is_dir() or not (root / "yonder.db").is_file():
        raise ValueError("Expected local gateway database is missing")
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "data"
        target.mkdir()
        for source in sorted(root.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Unexpected symlink in data volume: {source.relative_to(root)}")
            dest = target / source.relative_to(root)
            if source.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            if not source.is_file():
                raise ValueError(f"Unsupported data entry: {source.relative_to(root)}")
            if source.name.endswith(("-wal", "-shm", "-journal")):
                base = source.with_name(source.name.rsplit("-", 1)[0])
                if base.suffix.lower() in (".db", ".sqlite", ".sqlite3") and base.is_file():
                    continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
                    with closing(sqlite3.connect(dest)) as dst:
                        src.backup(dst)
                        dst.execute("PRAGMA journal_mode=DELETE")
                        rows = dst.execute("PRAGMA integrity_check").fetchall()
                        if rows != [("ok",)]:
                            raise ValueError(f"Database integrity check failed: {source.name}")
            else:
                shutil.copy2(source, dest)
        with tarfile.open(fileobj=output, mode="w|gz") as archive:
            archive.add(target, arcname="data")


if __name__ == "__main__":
    archive_data(Path("/app/data"), sys.stdout.buffer)
