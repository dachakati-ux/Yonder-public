"""SQLAlchemy-backed Academy progress store."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import LessonProgressRow
from app.services.progress import LessonProgress


def _now() -> datetime:
    return datetime.now(UTC)


class SqlAlchemyProgressStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _to_model(self, row: LessonProgressRow) -> LessonProgress:
        return LessonProgress(
            lesson_id=row.lesson_id,
            principal_subject=row.principal_subject,
            status=row.status,  # type: ignore[arg-type]
            check_attempts=row.check_attempts,
            check_passed=row.check_passed,
            last_check_correct=row.last_check_correct,
            last_try_it_request_id=row.last_try_it_request_id,
            try_it_recorded=row.try_it_recorded,
            completed_at=row.completed_at,
            updated_at=row.updated_at,
            created_at=row.created_at,
        )

    async def get_progress(
        self, principal_subject: str, lesson_id: str
    ) -> LessonProgress | None:
        with self._session_factory() as session:
            row = session.get(LessonProgressRow, (principal_subject, lesson_id))
            return self._to_model(row) if row else None

    async def list_progress(self, principal_subject: str) -> list[LessonProgress]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(LessonProgressRow).where(
                    LessonProgressRow.principal_subject == principal_subject
                )
            ).all()
        return sorted((self._to_model(r) for r in rows), key=lambda p: p.lesson_id)

    async def list_all_progress(self) -> list[LessonProgress]:
        with self._session_factory() as session:
            rows = session.scalars(select(LessonProgressRow)).all()
        return sorted(
            (self._to_model(r) for r in rows),
            key=lambda p: (p.principal_subject, p.lesson_id),
        )

    async def upsert_progress(self, progress: LessonProgress) -> LessonProgress:
        with self._session_factory() as session:
            row = session.get(
                LessonProgressRow, (progress.principal_subject, progress.lesson_id)
            )
            if row is None:
                row = LessonProgressRow(
                    principal_subject=progress.principal_subject,
                    lesson_id=progress.lesson_id,
                    created_at=progress.created_at,
                )
                session.add(row)
            row.status = progress.status
            row.check_attempts = progress.check_attempts
            row.check_passed = progress.check_passed
            row.last_check_correct = progress.last_check_correct
            row.last_try_it_request_id = progress.last_try_it_request_id
            row.try_it_recorded = progress.try_it_recorded
            row.completed_at = progress.completed_at
            row.updated_at = progress.updated_at
            session.commit()
            session.refresh(row)
            return self._to_model(row)

    async def reset_progress(self, principal_subject: str) -> int:
        with self._session_factory() as session:
            rows = session.scalars(
                select(LessonProgressRow).where(
                    LessonProgressRow.principal_subject == principal_subject
                )
            ).all()
            count = len(rows)
            for row in rows:
                session.delete(row)
            session.commit()
            return count
