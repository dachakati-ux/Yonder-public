"""SQLAlchemy durability for capacity / EnsureReady usage events."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import CapacityUsageEventRow


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class InMemoryCapacityUsageStore:
    """Ring buffer used when database_url is unset."""

    def __init__(self, *, limit: int = 500) -> None:
        self._limit = limit
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": str(event.get("id") or f"cap-{uuid.uuid4().hex[:12]}"),
            "ts": event.get("ts") or _iso(_now()),
            "event_type": str(event.get("event_type") or "unknown"),
            "model_definition_id": event.get("model_definition_id"),
            "deployment_id": event.get("deployment_id"),
            "worker_id": event.get("worker_id"),
            "gpu_indexes": list(event.get("gpu_indexes") or []),
            "reason": event.get("reason"),
            "request_id": event.get("request_id"),
            "principal_subject": event.get("principal_subject"),
            "details": dict(event.get("details") or {}),
        }
        with self._lock:
            self._events.insert(0, record)
            del self._events[self._limit :]
        return record

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events[:limit]]


class SqlAlchemyCapacityUsageStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        memory_limit: int = 500,
    ) -> None:
        self._session_factory = session_factory
        self._memory = InMemoryCapacityUsageStore(limit=memory_limit)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        record = self._memory.append(event)
        with self._session_factory() as session:
            row = CapacityUsageEventRow(
                id=record["id"],
                ts=_now(),
                event_type=record["event_type"],
                model_definition_id=record.get("model_definition_id"),
                deployment_id=record.get("deployment_id"),
                worker_id=record.get("worker_id"),
                gpu_indexes=list(record.get("gpu_indexes") or []),
                reason=record.get("reason"),
                request_id=record.get("request_id"),
                principal_subject=record.get("principal_subject"),
                details=dict(record.get("details") or {}),
            )
            session.add(row)
            session.commit()
        return record

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CapacityUsageEventRow)
                .order_by(CapacityUsageEventRow.ts.desc())
                .limit(limit)
            ).all()
            if rows:
                return [
                    {
                        "id": row.id,
                        "ts": _iso(row.ts),
                        "event_type": row.event_type,
                        "model_definition_id": row.model_definition_id,
                        "deployment_id": row.deployment_id,
                        "worker_id": row.worker_id,
                        "gpu_indexes": list(row.gpu_indexes or []),
                        "reason": row.reason,
                        "request_id": row.request_id,
                        "principal_subject": row.principal_subject,
                        "details": dict(row.details or {}),
                    }
                    for row in rows
                ]
        return self._memory.list_events(limit=limit)
