"""Durable gateway state for aliases and deployments (survives restart)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.routing.models import DeploymentRecord, DeploymentState, ModelAliasRoute, RouteState
from app.routing.registry import RouteRegistry


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


class StateStore(Protocol):
    def load_into(self, routes: RouteRegistry) -> None: ...

    def save_from(self, routes: RouteRegistry) -> None: ...

    def has_persisted_state(self) -> bool: ...


class JsonFileStateStore:
    """Simple durable store used when a database is not configured.

    Model *definitions* continue to load from manifests on disk.
    Alias and deployment runtime state are persisted here so gateway restart
    does not reset load/unload/drain outcomes or alias repoints.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def has_persisted_state(self) -> bool:
        return self.path.exists()

    def load_into(self, routes: RouteRegistry) -> None:
        if not self.path.exists():
            return
        with self._lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        for raw in data.get("deployments", []):
            if "status" in raw and isinstance(raw["status"], str):
                raw["status"] = DeploymentState(raw["status"])
            # Runtime counters must not carry over as active after restart.
            raw["active_requests"] = 0
            raw["queue_depth"] = 0
            routes.upsert_deployment(DeploymentRecord.model_validate(raw))
        for raw in data.get("aliases", []):
            if "state" in raw and isinstance(raw["state"], str):
                raw["state"] = RouteState(raw["state"])
            routes.upsert_alias(ModelAliasRoute.model_validate(raw))

    def save_from(self, routes: RouteRegistry) -> None:
        payload = {
            "version": 1,
            "deployments": [
                deployment.model_dump(mode="json")
                for deployment in routes.deployments.values()
            ],
            "aliases": [
                alias.model_dump(mode="json") for alias in routes.aliases.values()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(
                json.dumps(payload, indent=2, default=_json_default) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.path)
