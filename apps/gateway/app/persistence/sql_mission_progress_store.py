"""SQLAlchemy-backed Academy mission progress store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import MissionProgressRow
from app.services.mission_progress import (
    MissionProgress,
    apply_mission_event,
    mission_public_overlay,
)


class SqlAlchemyMissionProgressStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _to_model(self, row: MissionProgressRow) -> MissionProgress:
        return MissionProgress(
            mission_id=row.mission_id,
            principal_subject=row.principal_subject,
            status=row.status,
            completed_steps=list(row.completed_steps or []),
            check_passed=row.check_passed,
            updated_at=row.updated_at,
        )

    def get(self, principal_subject: str, mission_id: str) -> MissionProgress | None:
        with self._session_factory() as session:
            row = session.get(MissionProgressRow, (principal_subject, mission_id))
            return self._to_model(row) if row else None

    def list_for(self, principal_subject: str) -> list[MissionProgress]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(MissionProgressRow).where(
                    MissionProgressRow.principal_subject == principal_subject
                )
            ).all()
        return sorted((self._to_model(r) for r in rows), key=lambda p: p.mission_id)

    def upsert(self, progress: MissionProgress) -> MissionProgress:
        with self._session_factory() as session:
            row = session.get(
                MissionProgressRow, (progress.principal_subject, progress.mission_id)
            )
            if row is None:
                row = MissionProgressRow(
                    principal_subject=progress.principal_subject,
                    mission_id=progress.mission_id,
                    updated_at=progress.updated_at,
                )
                session.add(row)
            row.status = progress.status
            row.completed_steps = list(progress.completed_steps)
            row.check_passed = progress.check_passed
            row.updated_at = progress.updated_at
            session.commit()
            session.refresh(row)
            return self._to_model(row)

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
