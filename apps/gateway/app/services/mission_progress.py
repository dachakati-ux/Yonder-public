"""In-memory mission progress keyed by RequestPrincipal.subject."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MissionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: str
    principal_subject: str
    status: str = "not_started"
    completed_steps: list[str] = Field(default_factory=list)
    check_passed: bool = False
    updated_at: str


class MissionProgressStoreCore(Protocol):
    def get(self, principal_subject: str, mission_id: str) -> MissionProgress | None: ...

    def upsert(self, progress: MissionProgress) -> MissionProgress: ...

    def list_for(self, principal_subject: str) -> list[MissionProgress]: ...


def apply_mission_event(
    store: MissionProgressStoreCore,
    *,
    principal_subject: str,
    mission_id: str,
    event: str,
    step_id: str | None = None,
    check_passed: bool | None = None,
) -> MissionProgress:
    row = store.get(principal_subject, mission_id) or MissionProgress(
        mission_id=mission_id,
        principal_subject=principal_subject,
        updated_at=_now(),
    )
    if event == "start":
        if row.status == "not_started":
            row = row.model_copy(update={"status": "in_progress", "updated_at": _now()})
    elif event == "step":
        if not step_id:
            raise ValueError("step_id_required")
        steps = list(row.completed_steps)
        if step_id not in steps:
            steps.append(step_id)
        row = row.model_copy(
            update={
                "status": "in_progress" if row.status != "completed" else row.status,
                "completed_steps": steps,
                "updated_at": _now(),
            }
        )
    elif event == "check":
        passed = bool(check_passed)
        row = row.model_copy(
            update={
                "check_passed": row.check_passed or passed,
                "status": "completed" if passed else "in_progress",
                "updated_at": _now(),
            }
        )
    else:
        raise ValueError(f"unknown_event:{event}")
    return store.upsert(row)


def mission_public_overlay(
    store: MissionProgressStoreCore, principal_subject: str
) -> dict[str, Any]:
    return {
        p.mission_id: {
            "status": p.status,
            "completed_steps": p.completed_steps,
            "check_passed": p.check_passed,
        }
        for p in store.list_for(principal_subject)
    }


class InMemoryMissionProgressStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], MissionProgress] = {}

    def get(self, principal_subject: str, mission_id: str) -> MissionProgress | None:
        return self._rows.get((principal_subject, mission_id))

    def list_for(self, principal_subject: str) -> list[MissionProgress]:
        return sorted(
            (p for (subj, _), p in self._rows.items() if subj == principal_subject),
            key=lambda p: p.mission_id,
        )

    def upsert(self, progress: MissionProgress) -> MissionProgress:
        self._rows[(progress.principal_subject, progress.mission_id)] = progress
        return progress

    def apply_event(
        self,
        *,
        principal_subject: str,
        mission_id: str,
        event: str,
        step_id: str | None = None,
        check_passed: bool | None = None,
    ) -> MissionProgress:
        return apply_mission_event(
            self,
            principal_subject=principal_subject,
            mission_id=mission_id,
            event=event,
            step_id=step_id,
            check_passed=check_passed,
        )

    def public_overlay(self, principal_subject: str) -> dict[str, Any]:
        return mission_public_overlay(self, principal_subject)
