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
    ) -> None:
        self.db_path = db_path
        self.max_projects_per_user = max_projects_per_user
        self.max_quota_mb_per_project = max_quota_mb_per_project
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
                if tenant_id is not None and item["tenant_id"] != tenant_id:
                    continue
                if item["owner_subject"] == owner_subject:
                    visible.append(item)
                elif is_teacher and item["class_id"] and item["class_id"] in class_ids:
                    visible.append(item)
            return visible

    def delete_project(self, project_id: str, *, actor_subject: str) -> bool:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM workspace_projects WHERE project_id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None:
                return False
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
                    "SELECT * FROM workspace_audit ORDER BY event_id DESC LIMIT ?",
                    (min(max(limit, 1), 200),),
                ).fetchall()
            return [dict(row) for row in rows]
