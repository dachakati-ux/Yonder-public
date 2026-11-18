"""Principal-scoped Academy lesson progress — auth-ready subject keying."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

LessonStatus = Literal["not_started", "in_progress", "completed"]
ProgressEventType = Literal["start", "check", "try_it", "complete"]


def _now() -> datetime:
    return datetime.now(UTC)


class LessonProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    principal_subject: str
    status: LessonStatus = "not_started"
    check_attempts: int = 0
    check_passed: bool = False
    last_check_correct: bool | None = None
    last_try_it_request_id: str | None = None
    try_it_recorded: bool = False
    completed_at: datetime | None = None
    updated_at: datetime
    created_at: datetime


class ProgressEventResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    status: LessonStatus
    check_passed: bool
    try_it_recorded: bool
    check_correct: bool | None = None
    explanation: str | None = None
    completed: bool = False
    progress: LessonProgress


class ProgressStore(Protocol):
    async def get_progress(
        self, principal_subject: str, lesson_id: str
    ) -> LessonProgress | None: ...

    async def list_progress(self, principal_subject: str) -> list[LessonProgress]: ...

    async def list_all_progress(self) -> list[LessonProgress]: ...

    async def upsert_progress(self, progress: LessonProgress) -> LessonProgress: ...

    async def reset_progress(self, principal_subject: str) -> int: ...


class InMemoryProgressStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], LessonProgress] = {}

    async def get_progress(
        self, principal_subject: str, lesson_id: str
    ) -> LessonProgress | None:
        return self._rows.get((principal_subject, lesson_id))

    async def list_progress(self, principal_subject: str) -> list[LessonProgress]:
        items = [p for (subj, _), p in self._rows.items() if subj == principal_subject]
        return sorted(items, key=lambda p: p.lesson_id)

    async def list_all_progress(self) -> list[LessonProgress]:
        return sorted(self._rows.values(), key=lambda p: (p.principal_subject, p.lesson_id))

    async def upsert_progress(self, progress: LessonProgress) -> LessonProgress:
        self._rows[(progress.principal_subject, progress.lesson_id)] = progress
        return progress

    async def reset_progress(self, principal_subject: str) -> int:
        keys = [k for k in self._rows if k[0] == principal_subject]
        for key in keys:
            del self._rows[key]
        return len(keys)


def empty_progress(principal_subject: str, lesson_id: str) -> LessonProgress:
    now = _now()
    return LessonProgress(
        lesson_id=lesson_id,
        principal_subject=principal_subject,
        status="not_started",
        updated_at=now,
        created_at=now,
    )


def progress_overlay(
    *,
    lesson_ids: list[str],
    rows: list[LessonProgress],
) -> dict[str, dict[str, Any]]:
    by_id = {row.lesson_id: row for row in rows}
    out: dict[str, dict[str, Any]] = {}
    for lid in lesson_ids:
        row = by_id.get(lid)
        if row is None:
            out[lid] = {
                "status": "not_started",
                "check_passed": False,
                "try_it_recorded": False,
                "check_attempts": 0,
            }
        else:
            out[lid] = {
                "status": row.status,
                "check_passed": row.check_passed,
                "try_it_recorded": row.try_it_recorded,
                "check_attempts": row.check_attempts,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
    return out


def track_completion_stats(
    lesson_ids: list[str], overlay: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    total = len(lesson_ids)
    completed = sum(1 for lid in lesson_ids if overlay.get(lid, {}).get("status") == "completed")
    return {
        "total": total,
        "completed": completed,
        "percent": int(round(100 * completed / total)) if total else 0,
    }
