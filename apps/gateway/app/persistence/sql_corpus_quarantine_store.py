"""SQLAlchemy-backed teaching corpus quarantine flags."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import CorpusQuarantineRow


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SqlAlchemyCorpusQuarantineStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list(self) -> list[str]:
        with self._session_factory() as session:
            rows = session.scalars(select(CorpusQuarantineRow)).all()
        return sorted(r.corpus_id for r in rows)

    def quarantine(self, corpus_id: str) -> None:
        with self._session_factory() as session:
            row = session.get(CorpusQuarantineRow, corpus_id)
            if row is None:
                session.add(
                    CorpusQuarantineRow(corpus_id=corpus_id, quarantined_at=_now())
                )
                session.commit()

    def release(self, corpus_id: str) -> None:
        with self._session_factory() as session:
            row = session.get(CorpusQuarantineRow, corpus_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def is_quarantined(self, corpus_id: str) -> bool:
        with self._session_factory() as session:
            return session.get(CorpusQuarantineRow, corpus_id) is not None
