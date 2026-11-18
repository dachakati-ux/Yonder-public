"""Worker registry helpers: heartbeat expiry and maintenance."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def apply_heartbeat_expiry(
    workers: dict[str, dict[str, Any]],
    *,
    timeout_seconds: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return worker records with connectivity derived from last heartbeat.

    Does not fabricate hardware metrics. When disconnected, hardware remains the
    last reported snapshot with an explicit connectivity status.
    """

    current = now or datetime.now(UTC)
    views: list[dict[str, Any]] = []
    for worker_id, record in workers.items():
        view = dict(record)
        # Explicit disconnect notifications win until a fresh heartbeat arrives.
        if (
            record.get("status") == "disconnected"
            and record.get("connectivity_reason") == "worker_disconnect"
        ):
            view["connected"] = False
            view["ready"] = False
            view["status"] = "disconnected"
            view["heartbeat_timeout_seconds"] = timeout_seconds
            last = _parse_ts(record.get("last_heartbeat"))
            if last is not None:
                view["heartbeat_age_seconds"] = (
                    current - last.astimezone(UTC)
                ).total_seconds()
            else:
                view["heartbeat_age_seconds"] = None
            views.append(view)
            workers[worker_id]["status"] = "disconnected"
            continue

        last = _parse_ts(record.get("last_heartbeat"))
        if last is None:
            view["status"] = "disconnected"
            view["connected"] = False
            view["connectivity_reason"] = "not_reported"
            view["heartbeat_age_seconds"] = None
        else:
            age = (current - last.astimezone(UTC)).total_seconds()
            view["heartbeat_age_seconds"] = age
            if age > timeout_seconds:
                view["status"] = "disconnected"
                view["connected"] = False
                view["connectivity_reason"] = "heartbeat_expired"
                view["ready"] = False
            else:
                view["status"] = "connected"
                view["connected"] = True
                view["connectivity_reason"] = "heartbeat_ok"
        view["heartbeat_timeout_seconds"] = timeout_seconds
        view["maintenance"] = bool(view.get("maintenance_state"))
        views.append(view)
        workers[worker_id]["status"] = view["status"]
    return sorted(views, key=lambda item: item.get("id", ""))


def set_maintenance(
    workers: dict[str, dict[str, Any]],
    worker_id: str,
    *,
    maintenance: bool,
) -> dict[str, Any] | None:
    record = workers.get(worker_id)
    if record is None:
        return None
    record["maintenance_state"] = maintenance
    record["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return record
