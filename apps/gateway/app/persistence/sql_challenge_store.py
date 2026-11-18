"""SQLAlchemy-backed Academy challenge submission store."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import ChallengeSubmissionRow
from app.services.cohorts import ChallengeSubmission


class SqlAlchemyChallengeStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _to_model(self, row: ChallengeSubmissionRow) -> ChallengeSubmission:
        return ChallengeSubmission(
            challenge_id=row.challenge_id,
            principal_subject=row.principal_subject,
            answer=row.answer,
            criteria_met=list(row.criteria_met or []),
            criteria_missed=list(row.criteria_missed or []),
            passed=row.passed,
            submitted_at=row.submitted_at,
        )

    def submit(self, submission: ChallengeSubmission) -> ChallengeSubmission:
        with self._session_factory() as session:
            row = session.get(
                ChallengeSubmissionRow,
                (submission.principal_subject, submission.challenge_id),
            )
            if row is None:
                row = ChallengeSubmissionRow(
                    principal_subject=submission.principal_subject,
                    challenge_id=submission.challenge_id,
                    answer=submission.answer,
                    submitted_at=submission.submitted_at,
                )
                session.add(row)
            row.answer = submission.answer
            row.criteria_met = list(submission.criteria_met)
            row.criteria_missed = list(submission.criteria_missed)
            row.passed = submission.passed
            row.submitted_at = submission.submitted_at
            session.commit()
            session.refresh(row)
            return self._to_model(row)

    def get(self, principal_subject: str, challenge_id: str) -> ChallengeSubmission | None:
        with self._session_factory() as session:
            row = session.get(ChallengeSubmissionRow, (principal_subject, challenge_id))
            return self._to_model(row) if row else None

    def list_for(self, principal_subject: str) -> list[ChallengeSubmission]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ChallengeSubmissionRow).where(
                    ChallengeSubmissionRow.principal_subject == principal_subject
                )
            ).all()
        return [self._to_model(r) for r in rows]

    def list_all(self) -> list[ChallengeSubmission]:
        with self._session_factory() as session:
            rows = session.scalars(select(ChallengeSubmissionRow)).all()
        return [self._to_model(r) for r in rows]

    def leaderboard(self, challenge_id: str) -> list[dict[str, Any]]:
        rows = [
            s for s in self.list_all() if s.challenge_id == challenge_id and s.passed
        ]
        rows.sort(key=lambda s: s.submitted_at)
        return [
            {
                "principal_subject": s.principal_subject,
                "passed": True,
                "criteria_met": s.criteria_met,
                "submitted_at": s.submitted_at,
            }
            for s in rows
        ]
