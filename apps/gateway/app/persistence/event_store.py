"""Admin event and request ledger persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.db_models import AdminEventRow, RequestLedgerRow
from app.services.request_ledger import RequestLedger, RequestRecord


def _parse_created_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)


class EventStore:
    """Append-only admin event store with optional SQL durability."""

    def __init__(
        self,
        *,
        limit: int = 500,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.limit = limit
        self._session_factory = session_factory
        self._memory: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        if "created_at" not in payload:
            payload["created_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._memory.append(payload)
        if len(self._memory) > self.limit:
            self._memory = self._memory[-self.limit :]
        if self._session_factory is not None:
            with self._session_factory() as session:
                session.add(
                    AdminEventRow(
                        created_at=_parse_created_at(payload.get("created_at")),
                        event_type=str(payload.get("event_type", "unknown")),
                        resource_type=payload.get("resource_type"),
                        resource_id=payload.get("resource_id"),
                        principal_subject=payload.get("principal_subject"),
                        request_id=payload.get("request_id"),
                        result=payload.get("result"),
                        payload=payload,
                    )
                )
                # Bound durable growth to the same soft limit.
                ids = (
                    session.execute(
                        select(AdminEventRow.id).order_by(AdminEventRow.id.desc())
                    )
                    .scalars()
                    .all()
                )
                if len(ids) > self.limit:
                    stale = ids[self.limit :]
                    session.execute(delete(AdminEventRow).where(AdminEventRow.id.in_(stale)))
                session.commit()
        return payload

    def list(
        self,
        *,
        limit: int | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._session_factory is not None:
            with self._session_factory() as session:
                stmt = select(AdminEventRow).order_by(AdminEventRow.id.desc())
                if event_type:
                    stmt = stmt.where(AdminEventRow.event_type == event_type)
                if limit is not None:
                    stmt = stmt.limit(limit)
                rows = session.execute(stmt).scalars().all()
                return [dict(row.payload) for row in rows]

        items = list(reversed(self._memory))
        if event_type:
            items = [item for item in items if item.get("event_type") == event_type]
        if limit is not None:
            items = items[:limit]
        return items


class DurableRequestLedger(RequestLedger):
    """Request ledger that mirrors entries into SQL when configured."""

    def __init__(
        self,
        *,
        limit: int = 200,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        super().__init__(limit=limit)
        self._session_factory = session_factory

    def add(self, record: RequestRecord) -> None:
        super().add(record)
        if self._session_factory is None:
            return
        with self._session_factory() as session:
            session.add(
                RequestLedgerRow(
                    request_id=record.request_id,
                    endpoint=record.endpoint,
                    model_alias=record.model_alias,
                    deployment_id=record.deployment_id,
                    worker_id=record.worker_id,
                    status=record.status,
                    error_code=record.error_code,
                    message=record.message,
                    duration_ms=record.duration_ms,
                    ttft_ms=record.ttft_ms,
                    created_at=_parse_created_at(record.created_at),
                )
            )
            ids = (
                session.execute(
                    select(RequestLedgerRow.id).order_by(RequestLedgerRow.id.desc())
                )
                .scalars()
                .all()
            )
            if len(ids) > self.limit:
                stale = ids[self.limit :]
                session.execute(
                    delete(RequestLedgerRow).where(RequestLedgerRow.id.in_(stale))
                )
            session.commit()

    def list(self, *, failures_only: bool = False) -> list[dict[str, Any]]:
        if self._session_factory is None:
            return super().list(failures_only=failures_only)
        with self._session_factory() as session:
            stmt = select(RequestLedgerRow).order_by(RequestLedgerRow.id.desc())
            if failures_only:
                stmt = stmt.where(RequestLedgerRow.status == "error")
            stmt = stmt.limit(self.limit)
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "request_id": row.request_id,
                    "endpoint": row.endpoint,
                    "model_alias": row.model_alias,
                    "deployment_id": row.deployment_id,
                    "worker_id": row.worker_id,
                    "status": row.status,
                    "error_code": row.error_code,
                    "message": row.message,
                    "duration_ms": row.duration_ms,
                    "ttft_ms": row.ttft_ms,
                    "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
                }
                for row in rows
            ]

    def get(self, request_id: str) -> dict[str, Any] | None:
        if self._session_factory is None:
            return super().get(request_id)
        with self._session_factory() as session:
            row = session.execute(
                select(RequestLedgerRow)
                .where(RequestLedgerRow.request_id == request_id)
                .order_by(RequestLedgerRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "request_id": row.request_id,
                "endpoint": row.endpoint,
                "model_alias": row.model_alias,
                "deployment_id": row.deployment_id,
                "worker_id": row.worker_id,
                "status": row.status,
                "error_code": row.error_code,
                "message": row.message,
                "duration_ms": row.duration_ms,
                "ttft_ms": row.ttft_ms,
                "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
            }
