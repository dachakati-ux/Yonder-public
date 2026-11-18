"""In-memory recent request ledger for Operations diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class RequestRecord:
    request_id: str
    endpoint: str
    model_alias: str | None = None
    deployment_id: str | None = None
    worker_id: str | None = None
    status: str = "received"
    error_code: str | None = None
    message: str | None = None
    duration_ms: float | None = None
    ttft_ms: float | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "model_alias": self.model_alias,
            "deployment_id": self.deployment_id,
            "worker_id": self.worker_id,
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "ttft_ms": self.ttft_ms,
            "created_at": self.created_at,
        }


class RequestLedger:
    def __init__(self, *, limit: int = 200) -> None:
        self.limit = limit
        self._items: list[RequestRecord] = []

    def add(self, record: RequestRecord) -> None:
        self._items.append(record)
        if len(self._items) > self.limit:
            self._items = self._items[-self.limit :]

    def list(self, *, failures_only: bool = False) -> list[dict[str, Any]]:
        items = self._items
        if failures_only:
            items = [item for item in items if item.status == "error"]
        return [item.as_dict() for item in reversed(items)]

    def get(self, request_id: str) -> dict[str, Any] | None:
        for item in reversed(self._items):
            if item.request_id == request_id:
                return item.as_dict()
        return None

    def active_count(self) -> int:
        return sum(1 for item in self._items if item.status == "active")
