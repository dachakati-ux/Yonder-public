"""Durable SQLite operation ledger with idempotent queue and restart recovery."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

ACTIVE = ("queued", "preflight", "acquiring", "deploying", "verifying", "rolling_back", "removing")
BLOCKING = (*ACTIVE, "interrupted")


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
            db.execute(
                """CREATE TABLE IF NOT EXISTS desired_addons (
                    addon_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    image TEXT NOT NULL,
                    desired_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS addon_setup (
                    addon_id TEXT PRIMARY KEY,
                    values_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
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
                "AND status IN (" + ",".join("?" for _ in BLOCKING) + ")",
                (addon_id, *BLOCKING),
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


    def acknowledge(self, job_id: str, observed: str) -> dict:
        """Operator explicitly reviews interrupted state before allowing new work."""
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "interrupted":
                raise JobConflict("Only interrupted jobs may be acknowledged")
            db.execute(
                "UPDATE jobs SET status='acknowledged', message=?, updated_at=? WHERE id=?",
                ("Administrator reviewed observed container state: " + observed, utcnow(), job_id),
            )
            after = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row(after)


    def set_desired(self, addon_id: str, version: str, image: str, state: str) -> None:
        """Only successful controller operations should update desired-state records."""
        if state not in ("running", "absent"):
            raise ValueError("Unsupported desired state")
        with self.lock, self._db() as db:
            db.execute(
                "INSERT INTO desired_addons "
                "(addon_id,version,image,desired_state,updated_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(addon_id) DO UPDATE SET "
                "version=excluded.version, image=excluded.image, "
                "desired_state=excluded.desired_state, updated_at=excluded.updated_at",
                (addon_id, version, image, state, utcnow()),
            )

    def desired(self) -> list[dict]:
        with self.lock, self._db() as db:
            rows = db.execute(
                "SELECT addon_id,version,image,desired_state,updated_at "
                "FROM desired_addons ORDER BY addon_id"
            ).fetchall()
            return [dict(row) for row in rows]


    def get_setup(self, addon_id: str) -> dict[str, str]:
        with self.lock, self._db() as db:
            row = db.execute(
                "SELECT values_json FROM addon_setup WHERE addon_id=?", (addon_id,)
            ).fetchone()
            return json.loads(row["values_json"]) if row is not None else {}

    def save_setup(self, addon_id: str, values: dict[str, str]) -> None:
        with self.lock, self._db() as db:
            pending = db.execute(
                "SELECT id FROM jobs WHERE addon_id=? "
                "AND status IN (" + ",".join("?" for _ in BLOCKING) + ")",
                (addon_id, *BLOCKING),
            ).fetchone()
            if pending:
                raise JobConflict("Installation or recovery is active; setup is locked")
            running = db.execute(
                "SELECT desired_state FROM desired_addons WHERE addon_id=?",
                (addon_id,),
            ).fetchone()
            if running and running["desired_state"] == "running":
                raise JobConflict("Remove the existing deployment before changing setup")
            db.execute(
                "INSERT INTO addon_setup (addon_id, values_json, updated_at) "
                "VALUES (?,?,?) ON CONFLICT(addon_id) DO UPDATE SET "
                "values_json=excluded.values_json, updated_at=excluded.updated_at",
                (addon_id, json.dumps(values, sort_keys=True), utcnow()),
            )
