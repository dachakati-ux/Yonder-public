"""Durable, offline-only migration rehearsal journal.

Every record is tied to an approved contract, backup and immutable target
digest. Active rehearsals become interrupted on process restart; they are
never automatically resumed or promoted to live upgrade jobs.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JournalError(ValueError):
    pass


class MigrationJournal:
    def __init__(self, path: Path, *, recover_on_init: bool = True):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise JournalError("Migration journal must not be a symlink")
        if not self.path.exists():
            fd = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(fd)
        if not self.path.is_file():
            raise JournalError("Migration journal must be a regular file")
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise JournalError("Migration journal requires private 0600 permissions")
        self.lock = threading.RLock()
        with self.lock, self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS migration_rehearsals (
                    id TEXT PRIMARY KEY,
                    addon_id TEXT NOT NULL,
                    source_image TEXT NOT NULL,
                    target_image TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL,
                    backup_manifest_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    note TEXT NOT NULL,
                    sandbox_database_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            if recover_on_init:
                db.execute(
                    "UPDATE migration_rehearsals SET state='interrupted', "
                    "note='Process restarted during rehearsal; manual inspection required', "
                    "updated_at=? WHERE state='running'",
                    (_now(),),
                )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def start(
        self, addon_id: str, source_image: str, target_image: str,
        contract_sha256: str, backup_manifest_sha256: str,
    ) -> str:
        with self.lock, self._connect() as db:
            active = db.execute(
                "SELECT id FROM migration_rehearsals "
                "WHERE addon_id=? AND state IN ('running','interrupted')",
                (addon_id,),
            ).fetchone()
            if active:
                raise JournalError("Unresolved migration rehearsal requires review")
            identifier = str(uuid.uuid4())
            now = _now()
            db.execute(
                "INSERT INTO migration_rehearsals "
                "(id,addon_id,source_image,target_image,contract_sha256,"
                "backup_manifest_sha256,state,note,created_at,updated_at) "
                "VALUES (?,?,?,?,? ,?,'running','',?,?)",
                (
                    identifier, addon_id, source_image, target_image,
                    contract_sha256, backup_manifest_sha256, now, now,
                ),
            )
            return identifier

    def finish(
        self, identifier: str, state: str, *,
        sandbox_database_sha256: str | None = None,
    ) -> None:
        if state not in ("passed", "failed", "interrupted"):
            raise JournalError("Unsupported rehearsal result")
        if state == "passed" and (
            sandbox_database_sha256 is None
            or len(sandbox_database_sha256) != 64
        ):
            raise JournalError("Passing rehearsal requires a database checksum")
        with self.lock, self._connect() as db:
            updated = db.execute(
                "UPDATE migration_rehearsals "
                "SET state=?, sandbox_database_sha256=?,updated_at=? "
                "WHERE id=? AND state='running'",
                (state, sandbox_database_sha256, _now(), identifier),
            )
            if updated.rowcount != 1:
                raise JournalError("Rehearsal is not active; cannot overwrite evidence")

    def get(self, identifier: str) -> dict | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM migration_rehearsals WHERE id=?",
                (identifier,),
            ).fetchone()
            return dict(row) if row else None

    def acknowledge_interrupted(self, identifier: str) -> None:
        """Explicitly clear an offline interruption; never marks it passed."""
        with self.lock, self._connect() as db:
            updated = db.execute(
                "UPDATE migration_rehearsals "
                "SET state='acknowledged',updated_at=?,"
                "note='Operator acknowledged failed/interrupted sandbox; "
                "separate fresh rehearsal required' "
                "WHERE id=? AND state='interrupted'",
                (_now(), identifier),
            )
            if updated.rowcount != 1:
                raise JournalError("Only interrupted rehearsals may be acknowledged")
