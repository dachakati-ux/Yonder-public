"""SQLAlchemy-backed Academy workbench notes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import WorkbenchNoteRow
from app.services.workbench import WorkbenchNote


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SqlAlchemyWorkbenchStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        max_notes_per_principal: int = 40,
    ) -> None:
        self._session_factory = session_factory
        self.max_notes_per_principal = max_notes_per_principal

    def _to_model(self, row: WorkbenchNoteRow) -> WorkbenchNote:
        return WorkbenchNote(
            id=row.id,
            principal_subject=row.principal_subject,
            title=row.title,
            body=row.body,
            tags=list(row.tags or []),
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_notes(self, principal_subject: str) -> list[WorkbenchNote]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(WorkbenchNoteRow).where(
                    WorkbenchNoteRow.principal_subject == principal_subject
                )
            ).all()
        items = [self._to_model(r) for r in rows]
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
        with self._session_factory() as session:
            session.add(
                WorkbenchNoteRow(
                    id=note.id,
                    principal_subject=note.principal_subject,
                    title=note.title,
                    body=note.body,
                    tags=list(note.tags),
                    source=note.source,
                    created_at=note.created_at,
                    updated_at=note.updated_at,
                )
            )
            session.commit()
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
        with self._session_factory() as session:
            row = session.get(WorkbenchNoteRow, note_id)
            if row is None or row.principal_subject != principal_subject:
                return None
            if title is not None:
                row.title = title.strip() or "Untitled"
            if body is not None:
                row.body = body
            if tags is not None:
                row.tags = list(tags)
            if source is not None:
                row.source = source
            row.updated_at = _now()
            session.commit()
            session.refresh(row)
            return self._to_model(row)

    def delete(self, note_id: str, principal_subject: str) -> bool:
        with self._session_factory() as session:
            row = session.get(WorkbenchNoteRow, note_id)
            if row is None or row.principal_subject != principal_subject:
                return False
            session.delete(row)
            session.commit()
            return True

    def export(self, principal_subject: str) -> dict[str, Any]:
        notes = self.list_notes(principal_subject)
        return {
            "principal_subject": principal_subject,
            "exported_at": _now(),
            "notes": [n.model_dump() for n in notes],
            "note": "Export is principal-scoped. No scores are invented.",
        }
