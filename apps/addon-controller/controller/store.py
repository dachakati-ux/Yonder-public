"""Durable SQLite operation ledger with idempotent queue and restart recovery."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

ACTIVE = ("queued", "preflight", "deploying", "verifying", "removing")


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class JobConflict(ValueError):
    pass


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        return db

    def _initialize(self) -> None:
        with self.lock, self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    addon_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS jobs_by_addon
                   ON jobs(addon_id, created_at)"""
            )
            placeholders = ",".join("?" for _ in ACTIVE)
            db.execute(
                "UPDATE jobs SET status='interrupted', "
                "message='Controller restarted; review host state before retry', "
                "updated_at=? WHERE status IN (" + placeholders + ")",
                (utcnow(), *ACTIVE),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["plan"] = json.loads(result.pop("plan_json"))
        return result

    def create(
        self, key: str, addon_id: str, action: str, plan: dict
    ) -> tuple[dict, bool]:
        with self.lock, self._db() as db:
            existing = db.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                item = self._row(existing)
                if (
                    item["addon_id"] != addon_id
                    or item["action"] != action
                    or item["plan_sha256"] != plan["plan_sha256"]
                ):
                    raise JobConflict("Idempotency key was already used for another operation")
                return item, False
            pending = db.execute(
                "SELECT id FROM jobs WHERE addon_id=? "
                "AND status IN (" + ",".join("?" for _ in ACTIVE) + ")",
                (addon_id, *ACTIVE),
            ).fetchone()
            if pending:
                raise JobConflict("An operation is already active for this add-on")
            now = utcnow()
            item_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO jobs (id,idempotency_key,addon_id,action,plan_sha256,"
                "plan_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item_id, key, addon_id, action, plan["plan_sha256"],
                    json.dumps(plan, sort_keys=True), "queued", now, now,
                ),
            )
            row = db.execute("SELECT * FROM jobs WHERE id=?", (item_id,)).fetchone()
            return self._row(row), True

    def get(self, job_id: str) -> dict | None:
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row(row) if row is not None else None

    def list(self, limit: int = 40) -> list[dict]:
        with self.lock, self._db() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 100),),
            ).fetchall()
            return [self._row(row) for row in rows]

    def transition(self, job_id: str, status: str, message: str = "") -> None:
        with self.lock, self._db() as db:
            db.execute(
                "UPDATE jobs SET status=?, message=?, updated_at=? WHERE id=?",
                (status, message[:400], utcnow(), job_id),
            )
