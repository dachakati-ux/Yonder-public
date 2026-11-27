"""Durable SQLite metadata, quota, and audit store for optional Workspaces."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{1,63}$")
SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class WorkspaceStoreError(ValueError):
    pass


class QuotaExceededError(WorkspaceStoreError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WorkspaceStore:
    def __init__(
        self,
        db_path: Path,
        *,
        max_projects_per_user: int = 10,
        max_quota_mb_per_project: int = 2048,
        max_active_sessions_per_user: int = 2,
    ) -> None:
        self.db_path = db_path
        self.max_projects_per_user = max_projects_per_user
        self.max_quota_mb_per_project = max_quota_mb_per_project
        self.max_active_sessions_per_user = max_active_sessions_per_user
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.db_path.exists():
            fd = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self._conn() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS workspace_projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    tenant_id TEXT,
                    class_id TEXT,
                    quota_mb INTEGER NOT NULL,
                    retention_days INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS workspace_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    actor_subject TEXT NOT NULL,
                    tenant_id TEXT,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS workspace_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    tenant_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    template_digest TEXT NOT NULL,
                    cpu_cores INTEGER NOT NULL,
                    memory_mb INTEGER NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    backend_ref TEXT,
                    created_at TEXT NOT NULL,
                    stopped_at TEXT,
                    UNIQUE(project_id, idempotency_key)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS workspace_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    tenant_id TEXT,
                    label TEXT NOT NULL,
                    digest_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def record_audit(
        self,
        *,
        project_id: str | None,
        actor_subject: str,
        tenant_id: str | None,
        action: str,
        detail: str,
    ) -> None:
        with self._lock, self._conn() as db:
            db.execute(
                "INSERT INTO workspace_audit "
                "(project_id,actor_subject,tenant_id,action,detail,timestamp) "
                "VALUES (?,?,?,?,?,?)",
                (project_id, actor_subject, tenant_id, action, detail[:240], _now()),
            )

    def create_project(
        self,
        *,
        name: str,
        owner_subject: str,
        tenant_id: str | None,
        class_id: str | None,
        quota_mb: int,
        retention_days: int = 90,
    ) -> dict:
        if not NAME_RE.fullmatch(name.strip()):
            raise WorkspaceStoreError("Invalid project name")
        if tenant_id is not None and not SCOPE_RE.fullmatch(tenant_id):
            raise WorkspaceStoreError("Invalid tenant identifier")
        if class_id is not None and not SCOPE_RE.fullmatch(class_id):
            raise WorkspaceStoreError("Invalid class identifier")
        if not (1 <= quota_mb <= self.max_quota_mb_per_project):
            raise QuotaExceededError(
                f"Requested quota_mb must be between 1 and {self.max_quota_mb_per_project}"
            )
        if not (1 <= retention_days <= 3650):
            raise WorkspaceStoreError("retention_days must be between 1 and 3650")

        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT COUNT(*) AS cnt FROM workspace_projects "
                "WHERE owner_subject=? AND IFNULL(tenant_id,'')=IFNULL(?,'') "
                "AND deleted_at IS NULL",
                (owner_subject, tenant_id),
            ).fetchone()
            if int(row["cnt"]) >= self.max_projects_per_user:
                raise QuotaExceededError("Per-user active project quota exceeded")
            project_id = f"ws_{uuid.uuid4().hex[:16]}"
            now = _now()
            db.execute(
                "INSERT INTO workspace_projects "
                "(project_id,name,owner_subject,tenant_id,class_id,quota_mb,retention_days,"
                "created_at,deleted_at) VALUES (?,?,?,?,?,?,?,?,NULL)",
                (
                    project_id,
                    name.strip(),
                    owner_subject,
                    tenant_id,
                    class_id,
                    quota_mb,
                    retention_days,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO workspace_audit "
                "(project_id,actor_subject,tenant_id,action,detail,timestamp) "
                "VALUES (?,?,?,?,?,?)",
                (project_id, owner_subject, tenant_id, "project_created", name.strip(), now),
            )
            created = db.execute(
                "SELECT * FROM workspace_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            return dict(created)

    def get_project(self, project_id: str) -> dict | None:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM workspace_projects WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_projects_for_principal(
        self,
        *,
        owner_subject: str,
        tenant_id: str | None,
        class_ids: tuple[str, ...],
        is_teacher: bool,
    ) -> list[dict]:
        with self._lock, self._conn() as db:
            rows = db.execute(
                "SELECT * FROM workspace_projects WHERE deleted_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
            visible: list[dict] = []
            for row in rows:
                item = dict(row)
                if item["tenant_id"] != tenant_id:
                    continue
                if item["owner_subject"] == owner_subject:
                    visible.append(item)
                elif (
                    tenant_id is not None and is_teacher
                    and item["class_id"] and item["class_id"] in class_ids
                ):
                    visible.append(item)
            return visible

    def delete_project(self, project_id: str, *, actor_subject: str) -> bool:
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM workspace_projects WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None:
                return False
            active = db.execute(
                "SELECT 1 FROM workspace_sessions WHERE project_id=? "
                "AND status IN ('provisioning', 'running') LIMIT 1", (project_id,)
            ).fetchone()
            if active:
                raise WorkspaceStoreError("Stop and reconcile active sessions before deletion")
            now = _now()
            db.execute(
                "UPDATE workspace_projects SET deleted_at=? WHERE project_id=?",
                (now, project_id),
            )
            db.execute(
                "INSERT INTO workspace_audit "
                "(project_id,actor_subject,tenant_id,action,detail,timestamp) "
                "VALUES (?,?,?,?,?,?)",
                (
                    project_id, actor_subject, row["tenant_id"],
                    "project_deleted", "soft_delete", now,
                ),
            )
            return True

    def list_audit(self, *, tenant_id: str | None, limit: int = 100) -> list[dict]:
        with self._lock, self._conn() as db:
            if tenant_id is not None:
                rows = db.execute(
                    "SELECT * FROM workspace_audit WHERE tenant_id=? "
                    "ORDER BY event_id DESC LIMIT ?",
                    (tenant_id, min(max(limit, 1), 200)),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workspace_audit WHERE tenant_id IS NULL "
                    "ORDER BY event_id DESC LIMIT ?", (min(max(limit, 1), 200),)
                ).fetchall()
            return [dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict | None:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM workspace_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def list_sessions_for_project(self, project_id: str) -> list[dict]:
        with self._lock, self._conn() as db:
            rows = db.execute(
                "SELECT * FROM workspace_sessions WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def reserve_session(
        self,
        *,
        project_id: str,
        owner_subject: str,
        tenant_id: str | None,
        idempotency_key: str,
        template_digest: str,
        cpu_cores: int,
        memory_mb: int,
        ttl_seconds: int,
        backend: str,
    ) -> tuple[dict, bool]:
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute(
                "SELECT * FROM workspace_projects WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if (
                project is None or project["owner_subject"] != owner_subject
                or project["tenant_id"] != tenant_id
            ):
                raise WorkspaceStoreError("Project is no longer available to this owner")
            existing = db.execute(
                "SELECT * FROM workspace_sessions WHERE project_id=? AND idempotency_key=?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            active_cnt = db.execute(
                "SELECT COUNT(*) AS cnt FROM workspace_sessions "
                "WHERE owner_subject=? AND tenant_id IS ? "
                "AND status IN ('provisioning', 'running')",
                (owner_subject, tenant_id),
            ).fetchone()
            if int(active_cnt["cnt"]) >= self.max_active_sessions_per_user:
                raise QuotaExceededError("Per-user active session limit exceeded")
            session_id = f"wss_{uuid.uuid4().hex[:16]}"
            now = _now()
            db.execute(
                "INSERT INTO workspace_sessions "
                "(session_id,project_id,owner_subject,tenant_id,idempotency_key,"
                "template_digest,cpu_cores,memory_mb,ttl_seconds,status,backend,"
                "backend_ref,created_at,stopped_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'provisioning',?,NULL,?,NULL)",
                (
                    session_id,
                    project_id,
                    owner_subject,
                    tenant_id,
                    idempotency_key,
                    template_digest,
                    cpu_cores,
                    memory_mb,
                    ttl_seconds,
                    backend,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM workspace_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row), True

    def mark_session_running(self, session_id: str, backend_ref: str) -> dict:
        with self._lock, self._conn() as db:
            db.execute(
                "UPDATE workspace_sessions SET status='running', backend_ref=? "
                "WHERE session_id=?",
                (backend_ref, session_id),
            )
            row = db.execute(
                "SELECT * FROM workspace_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row)

    def mark_session_stopped(self, session_id: str, *, status: str = "stopped") -> dict:
        with self._lock, self._conn() as db:
            db.execute(
                "UPDATE workspace_sessions SET status=?, stopped_at=? WHERE session_id=?",
                (status, _now(), session_id),
            )
            row = db.execute(
                "SELECT * FROM workspace_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row)

    def create_snapshot(
        self,
        *,
        project_id: str,
        owner_subject: str,
        tenant_id: str | None,
        label: str,
        digest_sha256: str,
        max_snapshots: int = 20,
    ) -> dict:
        if not NAME_RE.fullmatch(label.strip()):
            raise WorkspaceStoreError("Invalid snapshot label")
        with self._lock, self._conn() as db:
            cnt = db.execute(
                "SELECT COUNT(*) AS c FROM workspace_snapshots WHERE project_id=?",
                (project_id,),
            ).fetchone()["c"]
            if int(cnt) >= max_snapshots:
                raise QuotaExceededError("Per-project snapshot quota exceeded")
            snapshot_id = f"snap_{uuid.uuid4().hex[:14]}"
            now = _now()
            db.execute(
                "INSERT INTO workspace_snapshots "
                "(snapshot_id,project_id,owner_subject,tenant_id,label,digest_sha256,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot_id, project_id, owner_subject, tenant_id,
                    label.strip(), digest_sha256, now,
                ),
            )
            db.execute(
                "INSERT INTO workspace_audit "
                "(project_id,actor_subject,tenant_id,action,detail,timestamp) "
                "VALUES (?,?,?,?,?,?)",
                (project_id, owner_subject, tenant_id, "snapshot_created", label.strip(), now),
            )
            row = db.execute(
                "SELECT * FROM workspace_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
            return dict(row)

    def list_snapshots(self, project_id: str) -> list[dict]:
        with self._lock, self._conn() as db:
            rows = db.execute(
                "SELECT * FROM workspace_snapshots WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]
