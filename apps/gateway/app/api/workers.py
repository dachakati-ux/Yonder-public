"""Worker registration endpoints on the gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.worker_trust import evaluate_worker_registration

router = APIRouter(prefix="/api/admin/workers", tags=["workers"])


def _require_worker_report(request: Request):
    principal = get_principal(request)
    if principal.has_capability("workers.report"):
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        "Principal lacks workers.report capability.",
        get_request_id(request),
        status_code=403,
        details={"subject": principal.subject},
    )


class WorkerHeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    display_name: str
    endpoint: str
    agent_version: str
    ready: bool = False
    maintenance: bool = False
    hardware: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    last_error: str | None = None


class WorkerRegistrationPayload(WorkerHeartbeatPayload):
    capabilities: list[str] = Field(default_factory=list)


@router.post("/register")
async def register_worker(payload: WorkerRegistrationPayload, request: Request):
    denied = _require_worker_report(request)
    if denied:
        return denied
    principal = get_principal(request)
    request_id = get_request_id(request)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    decision = evaluate_worker_registration(
        request.app.state.settings.worker_registration, payload.worker_id
    )
    if not decision["accepted"]:
        request.app.state.event_store.append(
            {
                "event_type": "worker.registration_rejected",
                "resource_type": "worker",
                "resource_id": payload.worker_id,
                "principal_subject": principal.subject,
                "request_id": request_id,
                "result": "rejected",
                "metadata": decision,
                "created_at": now,
            }
        )
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Worker registration rejected by trust policy.",
            request_id,
            status_code=403,
            details={
                "worker_id": payload.worker_id,
                "reason": decision["reason"],
                "trust_state": decision["trust_state"],
            },
        )

    record = {
        "id": payload.worker_id,
        "display_name": payload.display_name,
        "endpoint": payload.endpoint,
        "status": "connected",
        "ready": payload.ready and bool(decision["trusted"]),
        "maintenance_state": payload.maintenance,
        "agent_version": payload.agent_version,
        "hardware_summary": payload.hardware,
        "runtime_summary": payload.runtime,
        "last_heartbeat": now,
        "last_error": payload.last_error,
        "registered_at": now,
        "updated_at": now,
        "registration_mode": decision["registration_mode"],
        "trust_state": decision["trust_state"],
        "trusted": bool(decision["trusted"]),
        "registered_by": principal.subject,
        "capabilities": list(payload.capabilities),
    }
    request.app.state.workers[payload.worker_id] = record
    request.app.state.event_store.append(
        {
            "event_type": "worker.registered",
            "resource_type": "worker",
            "resource_id": payload.worker_id,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "accepted",
            "metadata": {
                "trust_state": decision["trust_state"],
                "trusted": decision["trusted"],
            },
            "created_at": now,
        }
    )
    request.app.state.metrics.worker_connected.labels(
        service="yonder-gateway", worker_id=payload.worker_id
    ).set(1 if decision["trusted"] else 0)
    return {"accepted": True, "worker": record, "request_id": request_id}


@router.post("/{worker_id}/heartbeat")
async def worker_heartbeat(
    worker_id: str, payload: WorkerHeartbeatPayload, request: Request
) -> dict[str, object]:
    denied = _require_worker_report(request)
    if denied:
        return denied
    principal = get_principal(request)
    request_id = get_request_id(request)
    if payload.worker_id != worker_id:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Heartbeat worker_id does not match the route worker_id.",
            request_id,
            status_code=400,
            details={
                "route_worker_id": worker_id,
                "payload_worker_id": payload.worker_id,
            },
        )
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    existing = request.app.state.workers.get(worker_id)
    if existing is None:
        return error_response(
            ErrorCode.WORKER_UNAVAILABLE,
            "Worker must register before sending heartbeats.",
            request_id,
            status_code=404,
            details={"worker_id": worker_id},
        )
    trusted = bool(existing.get("trusted", False))
    record = {
        **existing,
        "id": worker_id,
        "display_name": payload.display_name,
        "endpoint": payload.endpoint,
        "status": "connected",
        "ready": payload.ready and trusted,
        "maintenance_state": payload.maintenance,
        "agent_version": payload.agent_version,
        "hardware_summary": payload.hardware,
        "runtime_summary": payload.runtime,
        "last_heartbeat": now,
        "last_error": payload.last_error,
        "updated_at": now,
        "connectivity_reason": "heartbeat_ok",
        "trusted": trusted,
        "trust_state": existing.get("trust_state", "trusted" if trusted else "untrusted"),
    }
    request.app.state.workers[worker_id] = record
    request.app.state.metrics.worker_connected.labels(
        service="yonder-gateway", worker_id=worker_id
    ).set(1 if trusted else 0)
    return {
        "accepted": True,
        "worker_id": worker_id,
        "request_id": request_id,
        "principal_subject": principal.subject,
        "trusted": trusted,
    }


@router.post("/{worker_id}/disconnect")
async def worker_disconnect(worker_id: str, request: Request) -> dict[str, object]:
    """Immediate disconnect notification from a shutting-down worker agent."""

    denied = _require_worker_report(request)
    if denied:
        return denied
    principal = get_principal(request)
    request_id = get_request_id(request)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    existing = request.app.state.workers.get(worker_id)
    if existing is None:
        return error_response(
            ErrorCode.WORKER_UNAVAILABLE,
            "Worker must register before disconnecting.",
            request_id,
            status_code=404,
            details={"worker_id": worker_id},
        )
    record = {
        **existing,
        "status": "disconnected",
        "connected": False,
        "ready": False,
        "updated_at": now,
        "connectivity_reason": "worker_disconnect",
    }
    request.app.state.workers[worker_id] = record
    request.app.state.metrics.worker_connected.labels(
        service="yonder-gateway", worker_id=worker_id
    ).set(0)
    request.app.state.event_store.append(
        {
            "event_type": "worker.disconnected",
            "resource_type": "worker",
            "resource_id": worker_id,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "accepted",
            "created_at": now,
        }
    )
    return {
        "accepted": True,
        "worker_id": worker_id,
        "request_id": request_id,
    }
