"""Institutional Academy & Small-Business service (M06.1-M06.5)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")

ORG_TEMPLATES: dict[str, dict[str, Any]] = {
    "k12_school": {
        "label": "K-12 School",
        "retention_days": 180,
        "allow_student_workspaces": True,
        "allow_student_agents": False,
        "max_tokens_per_day_per_user": 25_000,
        "privacy_notice": (
            "Student prompts and submissions are scoped to enrolled classes, retained per "
            "district policy, and never used for external model training."
        ),
    },
    "university": {
        "label": "Higher Education / Lab",
        "retention_days": 365,
        "allow_student_workspaces": True,
        "allow_student_agents": True,
        "max_tokens_per_day_per_user": 100_000,
        "privacy_notice": (
            "Coursework and lab artifacts are isolated per course enrollment and subject to "
            "institutional retention and export controls."
        ),
    },
    "small_business": {
        "label": "Small Business / Team",
        "retention_days": 90,
        "allow_student_workspaces": True,
        "allow_student_agents": True,
        "max_tokens_per_day_per_user": 250_000,
        "privacy_notice": (
            "Team projects and audit logs are tenant-scoped with configurable retention and "
            "administrator export."
        ),
    },
}


class AcademyOrgError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class AcademyOrgStore:
    """Durable SQLite store for classes, enrollments, assignments, quotas, and privacy audit."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.db_path.exists():
            fd = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self._conn() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_orgs (
                    tenant_id TEXT PRIMARY KEY,
                    org_name TEXT NOT NULL,
                    profile_template TEXT NOT NULL,
                    retention_days INTEGER NOT NULL,
                    privacy_notice TEXT NOT NULL,
                    allowed_model_aliases_json TEXT NOT NULL,
                    max_tokens_per_day_per_user INTEGER NOT NULL,
                    allow_student_workspaces INTEGER NOT NULL,
                    allow_student_agents INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_classes (
                    class_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    instructor_subject TEXT NOT NULL,
                    model_guidance TEXT NOT NULL,
                    allowed_model_aliases_json TEXT NOT NULL,
                    max_workspace_quota_mb INTEGER NOT NULL,
                    retention_days INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_enrollments (
                    class_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    student_subject TEXT NOT NULL,
                    role TEXT NOT NULL,
                    enrolled_at TEXT NOT NULL,
                    enrolled_by TEXT NOT NULL,
                    PRIMARY KEY(class_id, student_subject)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    class_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    model_guidance TEXT NOT NULL,
                    due_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_submissions (
                    submission_id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL,
                    class_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    student_subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    token_usage_estimate INTEGER NOT NULL,
                    feedback TEXT NOT NULL,
                    submitted_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_daily_usage (
                    tenant_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL,
                    PRIMARY KEY(tenant_id, subject, usage_date)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS academy_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )"""
            )

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def _audit(
        self,
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        actor_subject: str,
        action: str,
        target: str,
        detail: str,
    ) -> None:
        db.execute(
            "INSERT INTO academy_audit (tenant_id,actor_subject,action,target,detail,timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (tenant_id, actor_subject, action, target[:128], detail[:240], _now()),
        )

    def get_org_settings(self, tenant_id: str) -> dict[str, Any]:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM academy_orgs WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            if row is None:
                tpl = ORG_TEMPLATES["k12_school"]
                return {
                    "tenant_id": tenant_id,
                    "org_name": tenant_id,
                    "profile_template": "k12_school",
                    "retention_days": tpl["retention_days"],
                    "privacy_notice": tpl["privacy_notice"],
                    "allowed_model_aliases": [],
                    "max_tokens_per_day_per_user": tpl["max_tokens_per_day_per_user"],
                    "allow_student_workspaces": tpl["allow_student_workspaces"],
                    "allow_student_agents": tpl["allow_student_agents"],
                    "configured": False,
                }
            data = dict(row)
            data["allowed_model_aliases"] = json.loads(data.pop("allowed_model_aliases_json"))
            data["allow_student_workspaces"] = bool(data["allow_student_workspaces"])
            data["allow_student_agents"] = bool(data["allow_student_agents"])
            data["configured"] = True
            return data

    def upsert_org_settings(
        self,
        *,
        tenant_id: str,
        org_name: str,
        profile_template: Literal["k12_school", "university", "small_business"],
        retention_days: int | None,
        privacy_notice: str | None,
        allowed_model_aliases: list[str],
        max_tokens_per_day_per_user: int | None,
        allow_student_workspaces: bool | None,
        allow_student_agents: bool | None,
        actor_subject: str,
    ) -> dict[str, Any]:
        if not SCOPE_RE.fullmatch(tenant_id):
            raise AcademyOrgError("Invalid tenant_id.")
        tpl = ORG_TEMPLATES[profile_template]
        eff_retention = retention_days if retention_days is not None else tpl["retention_days"]
        eff_notice = (
            privacy_notice.strip()
            if privacy_notice and privacy_notice.strip()
            else tpl["privacy_notice"]
        )
        eff_tokens = (
            max_tokens_per_day_per_user
            if max_tokens_per_day_per_user is not None
            else tpl["max_tokens_per_day_per_user"]
        )
        eff_ws = (
            allow_student_workspaces
            if allow_student_workspaces is not None
            else tpl["allow_student_workspaces"]
        )
        eff_agents = (
            allow_student_agents
            if allow_student_agents is not None
            else tpl["allow_student_agents"]
        )
        with self._lock, self._conn() as db:
            db.execute(
                "INSERT INTO academy_orgs "
                "(tenant_id,org_name,profile_template,retention_days,privacy_notice,"
                "allowed_model_aliases_json,max_tokens_per_day_per_user,"
                "allow_student_workspaces,allow_student_agents,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "org_name=excluded.org_name,profile_template=excluded.profile_template,"
                "retention_days=excluded.retention_days,privacy_notice=excluded.privacy_notice,"
                "allowed_model_aliases_json=excluded.allowed_model_aliases_json,"
                "max_tokens_per_day_per_user=excluded.max_tokens_per_day_per_user,"
                "allow_student_workspaces=excluded.allow_student_workspaces,"
                "allow_student_agents=excluded.allow_student_agents,"
                "updated_at=excluded.updated_at",
                (
                    tenant_id,
                    org_name.strip(),
                    profile_template,
                    eff_retention,
                    eff_notice,
                    json.dumps(allowed_model_aliases),
                    eff_tokens,
                    int(eff_ws),
                    int(eff_agents),
                    _now(),
                ),
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="org_settings_updated",
                target=tenant_id,
                detail=f"template={profile_template} retention={eff_retention}d",
            )
        return self.get_org_settings(tenant_id)

    def create_class(
        self,
        *,
        tenant_id: str,
        title: str,
        instructor_subject: str,
        model_guidance: str,
        allowed_model_aliases: list[str],
        max_workspace_quota_mb: int,
        retention_days: int,
        actor_subject: str,
    ) -> dict[str, Any]:
        class_id = f"cls_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as db:
            db.execute(
                "INSERT INTO academy_classes "
                "(class_id,tenant_id,title,instructor_subject,model_guidance,"
                "allowed_model_aliases_json,max_workspace_quota_mb,retention_days,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    class_id,
                    tenant_id,
                    title.strip(),
                    instructor_subject,
                    model_guidance.strip(),
                    json.dumps(allowed_model_aliases),
                    max_workspace_quota_mb,
                    retention_days,
                    _now(),
                ),
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="class_created",
                target=class_id,
                detail=title.strip(),
            )
            row = db.execute(
                "SELECT * FROM academy_classes WHERE class_id=?", (class_id,)
            ).fetchone()
            item = dict(row)
            item["allowed_model_aliases"] = json.loads(item.pop("allowed_model_aliases_json"))
            return item

    def get_class(self, class_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM academy_classes WHERE class_id=?", (class_id,)
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["allowed_model_aliases"] = json.loads(item.pop("allowed_model_aliases_json"))
            return item

    def list_classes_for_user(
        self, *, tenant_id: str, subject: str, is_admin: bool
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn() as db:
            if is_admin:
                rows = db.execute(
                    "SELECT * FROM academy_classes WHERE tenant_id=? ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT DISTINCT c.* FROM academy_classes c "
                    "LEFT JOIN academy_enrollments e ON c.class_id = e.class_id "
                    "WHERE c.tenant_id=? AND (c.instructor_subject=? OR e.student_subject=?) "
                    "ORDER BY c.created_at DESC",
                    (tenant_id, subject, subject),
                ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["allowed_model_aliases"] = json.loads(item.pop("allowed_model_aliases_json"))
                out.append(item)
            return out

    def enroll_student(
        self,
        *,
        class_id: str,
        tenant_id: str,
        student_subject: str,
        role: Literal["student", "ta"],
        actor_subject: str,
    ) -> dict[str, Any]:
        with self._lock, self._conn() as db:
            now = _now()
            db.execute(
                "INSERT INTO academy_enrollments "
                "(class_id,tenant_id,student_subject,role,enrolled_at,enrolled_by) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(class_id,student_subject) DO UPDATE SET role=excluded.role",
                (class_id, tenant_id, student_subject.strip(), role, now, actor_subject),
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="student_enrolled",
                target=class_id,
                detail=f"{student_subject.strip()} ({role})",
            )
            row = db.execute(
                "SELECT * FROM academy_enrollments WHERE class_id=? AND student_subject=?",
                (class_id, student_subject.strip()),
            ).fetchone()
            return dict(row)

    def is_enrolled(self, class_id: str, student_subject: str) -> bool:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT 1 FROM academy_enrollments WHERE class_id=? AND student_subject=?",
                (class_id, student_subject),
            ).fetchone()
            return row is not None

    def create_assignment(
        self,
        *,
        class_id: str,
        tenant_id: str,
        title: str,
        instructions: str,
        model_guidance: str,
        due_at: str | None,
        actor_subject: str,
    ) -> dict[str, Any]:
        assignment_id = f"asg_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as db:
            db.execute(
                "INSERT INTO academy_assignments "
                "(assignment_id,class_id,tenant_id,title,instructions,"
                "model_guidance,due_at,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    assignment_id,
                    class_id,
                    tenant_id,
                    title.strip(),
                    instructions.strip(),
                    model_guidance.strip(),
                    due_at,
                    actor_subject,
                    _now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM academy_assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            return dict(row)

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT * FROM academy_assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def list_assignments(self, class_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as db:
            rows = db.execute(
                "SELECT * FROM academy_assignments WHERE class_id=? ORDER BY created_at DESC",
                (class_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def submit_assignment(
        self,
        *,
        assignment_id: str,
        class_id: str,
        tenant_id: str,
        student_subject: str,
        summary: str,
        token_usage_estimate: int,
    ) -> dict[str, Any]:
        submission_id = f"sub_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as db:
            now = _now()
            db.execute(
                "INSERT INTO academy_submissions "
                "(submission_id,assignment_id,class_id,tenant_id,student_subject,"
                "status,summary,token_usage_estimate,feedback,submitted_at) "
                "VALUES (?,?,?,?,?,'submitted',?,?,?,?)",
                (
                    submission_id,
                    assignment_id,
                    class_id,
                    tenant_id,
                    student_subject,
                    summary.strip(),
                    token_usage_estimate,
                    "",
                    now,
                ),
            )
            day = _today()
            db.execute(
                "INSERT INTO academy_daily_usage (tenant_id,subject,usage_date,tokens_used) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(tenant_id,subject,usage_date) DO UPDATE SET "
                "tokens_used = academy_daily_usage.tokens_used + excluded.tokens_used",
                (tenant_id, student_subject, day, token_usage_estimate),
            )
            row = db.execute(
                "SELECT * FROM academy_submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
            return dict(row)

    def list_submissions(
        self, *, assignment_id: str, student_subject: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn() as db:
            if student_subject is not None:
                rows = db.execute(
                    "SELECT * FROM academy_submissions "
                    "WHERE assignment_id=? AND student_subject=? ORDER BY submitted_at DESC",
                    (assignment_id, student_subject),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM academy_submissions "
                    "WHERE assignment_id=? ORDER BY submitted_at DESC",
                    (assignment_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def check_usage_policy(
        self,
        *,
        tenant_id: str,
        class_id: str | None,
        subject: str,
        model_alias: str,
        requested_tokens: int,
        uses_agent: bool,
    ) -> dict[str, Any]:
        org = self.get_org_settings(tenant_id)
        if uses_agent and not org["allow_student_agents"]:
            return {
                "allowed": False,
                "reason": "Student agent execution is disabled by org policy.",
            }
        allowed_models = set(org["allowed_model_aliases"])
        if class_id:
            cls = self.get_class(class_id)
            if cls is not None and cls["allowed_model_aliases"]:
                allowed_models = set(cls["allowed_model_aliases"])
        if allowed_models and model_alias not in allowed_models:
            return {"allowed": False, "reason": f"Model '{model_alias}' is not approved for class."}
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT tokens_used FROM academy_daily_usage "
                "WHERE tenant_id=? AND subject=? AND usage_date=?",
                (tenant_id, subject, _today()),
            ).fetchone()
            used = int(row["tokens_used"]) if row is not None else 0
        limit = int(org["max_tokens_per_day_per_user"])
        if used + requested_tokens > limit:
            return {
                "allowed": False,
                "reason": f"Daily token quota exceeded ({used}/{limit}).",
            }
        return {"allowed": True, "reason": "Permitted.", "tokens_used_today": used, "limit": limit}

    def export_student_data(
        self, *, tenant_id: str, student_subject: str, actor_subject: str
    ) -> dict[str, Any]:
        org = self.get_org_settings(tenant_id)
        with self._lock, self._conn() as db:
            enrollments = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM academy_enrollments WHERE tenant_id=? AND student_subject=?",
                    (tenant_id, student_subject),
                ).fetchall()
            ]
            submissions = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM academy_submissions WHERE tenant_id=? AND student_subject=?",
                    (tenant_id, student_subject),
                ).fetchall()
            ]
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="student_data_exported",
                target=student_subject,
                detail=f"enrollments={len(enrollments)} submissions={len(submissions)}",
            )
        return {
            "tenant_id": tenant_id,
            "student_subject": student_subject,
            "privacy_notice": org["privacy_notice"],
            "enrollments": enrollments,
            "submissions": submissions,
        }

    def delete_student_data(
        self, *, tenant_id: str, student_subject: str, actor_subject: str
    ) -> dict[str, int]:
        with self._lock, self._conn() as db:
            cur1 = db.execute(
                "DELETE FROM academy_submissions WHERE tenant_id=? AND student_subject=?",
                (tenant_id, student_subject),
            )
            cur2 = db.execute(
                "DELETE FROM academy_enrollments WHERE tenant_id=? AND student_subject=?",
                (tenant_id, student_subject),
            )
            db.execute(
                "DELETE FROM academy_daily_usage WHERE tenant_id=? AND subject=?",
                (tenant_id, student_subject),
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="student_data_deleted",
                target=student_subject,
                detail=f"submissions={cur1.rowcount} enrollments={cur2.rowcount}",
            )
            return {"deleted_submissions": cur1.rowcount, "deleted_enrollments": cur2.rowcount}

    def purge_expired_submissions(self, *, tenant_id: str, actor_subject: str) -> int:
        org = self.get_org_settings(tenant_id)
        cutoff = (datetime.now(UTC) - timedelta(days=int(org["retention_days"]))).isoformat()
        with self._lock, self._conn() as db:
            cur = db.execute(
                "DELETE FROM academy_submissions WHERE tenant_id=? AND submitted_at < ?",
                (tenant_id, cutoff),
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_subject=actor_subject,
                action="retention_purge",
                target=tenant_id,
                detail=f"purged_submissions={cur.rowcount} cutoff={cutoff}",
            )
            return cur.rowcount

    def diagnostics(self, tenant_id: str) -> dict[str, Any]:
        org = self.get_org_settings(tenant_id)
        with self._lock, self._conn() as db:
            classes = db.execute(
                "SELECT COUNT(*) AS c FROM academy_classes WHERE tenant_id=?", (tenant_id,)
            ).fetchone()["c"]
            enrollments = db.execute(
                "SELECT COUNT(*) AS c FROM academy_enrollments WHERE tenant_id=?", (tenant_id,)
            ).fetchone()["c"]
            assignments = db.execute(
                "SELECT COUNT(*) AS c FROM academy_assignments WHERE tenant_id=?", (tenant_id,)
            ).fetchone()["c"]
            submissions = db.execute(
                "SELECT COUNT(*) AS c FROM academy_submissions WHERE tenant_id=?", (tenant_id,)
            ).fetchone()["c"]
            audit = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM academy_audit WHERE tenant_id=? ORDER BY event_id DESC LIMIT 50",
                    (tenant_id,),
                ).fetchall()
            ]
        return {
            "org": org,
            "counts": {
                "classes": classes,
                "enrollments": enrollments,
                "assignments": assignments,
                "submissions": submissions,
            },
            "recent_audit": audit,
        }
