"""Principal-scoped Academy workbench notes/artifacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class WorkbenchNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    principal_subject: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"
    created_at: str
    updated_at: str


class InMemoryWorkbenchStore:
    def __init__(self, *, max_notes_per_principal: int = 40) -> None:
        self._notes: dict[str, WorkbenchNote] = {}
        self.max_notes_per_principal = max_notes_per_principal

    def list_notes(self, principal_subject: str) -> list[WorkbenchNote]:
        items = [n for n in self._notes.values() if n.principal_subject == principal_subject]
        return sorted(items, key=lambda n: n.updated_at, reverse=True)

    def create(
        self,
        *,
        principal_subject: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
        source: str = "manual",
    ) -> WorkbenchNote:
        existing = self.list_notes(principal_subject)
        if len(existing) >= self.max_notes_per_principal:
            raise ValueError(
                f"Workbench note quota exceeded (max {self.max_notes_per_principal})."
            )
        now = _now()
        note = WorkbenchNote(
            id=f"note_{uuid.uuid4().hex[:12]}",
            principal_subject=principal_subject,
            title=title.strip() or "Untitled",
            body=body,
            tags=tags or [],
            source=source,
            created_at=now,
            updated_at=now,
        )
        self._notes[note.id] = note
        return note

    def update(
        self,
        note_id: str,
        principal_subject: str,
        *,
        title: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> WorkbenchNote | None:
        note = self._notes.get(note_id)
        if note is None or note.principal_subject != principal_subject:
            return None
        updates: dict[str, Any] = {"updated_at": _now()}
        if title is not None:
            updates["title"] = title.strip() or "Untitled"
        if body is not None:
            updates["body"] = body
        if tags is not None:
            updates["tags"] = tags
        if source is not None:
            updates["source"] = source
        updated = note.model_copy(update=updates)
        self._notes[note_id] = updated
        return updated

    def delete(self, note_id: str, principal_subject: str) -> bool:
        note = self._notes.get(note_id)
        if note is None or note.principal_subject != principal_subject:
            return False
        del self._notes[note_id]
        return True

    def export(self, principal_subject: str) -> dict[str, Any]:
        notes = self.list_notes(principal_subject)
        return {
            "principal_subject": principal_subject,
            "exported_at": _now(),
            "notes": [n.model_dump() for n in notes],
            "note": "Export is principal-scoped. No scores are invented.",
        }
