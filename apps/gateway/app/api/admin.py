"""Admin API namespace."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode
from yonder_contracts.principal import RequestPrincipal
from yonder_contracts.versioning import SERVICE_VERSION

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.models.openai import ChatCompletionRequest, ChatMessage
from app.routing.models import RouteState
from app.services.chat import ChatServiceError
from app.services.lifecycle import LifecycleError
from app.services.model_discovery import (
    ModelDiscoveryError,
    prepare_model_bundle,
    resolve_allowed_path,
    resolved_model_roots,
    scan_model_path,
)
from app.services.workers import apply_heartbeat_expiry, set_maintenance

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_capability(principal: RequestPrincipal, capability: str, request_id: str):
    if not principal.has_capability(capability):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            f"Principal lacks {capability} capability.",
            request_id,
            details={"subject": principal.subject},
        )
    return None


def _audit(request: Request, event: dict[str, Any]) -> None:
    request.app.state.event_store.append(event)


class LoadDeploymentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str | None = None
    model_definition_id: str | None = None
    worker_id: str | None = None
    placement_mode: str | None = None
    gpu_indexes: list[int] | None = None
    context_length: int | None = Field(default=None, gt=0)


class AliasUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    enabled: bool = True


class RouteEnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class TestPromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_alias: str = "yonder-chat"
    prompt: str = Field(min_length=1)
    stream: bool = False


class MaintenanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maintenance: bool = True


class ModelFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    force: bool = False


def _worker_views(request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    return apply_heartbeat_expiry(
        request.app.state.workers,
        timeout_seconds=settings.worker_heartbeat_timeout_seconds,
    )


@router.get("/workers")
async def list_workers(request: Request):
    principal: RequestPrincipal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.view", request_id)
    if denied:
        return denied
    views = _worker_views(request)
    for view in views:
        request.app.state.metrics.worker_connected.labels(
            service="yonder-gateway", worker_id=view["id"]
        ).set(1 if view.get("connected") else 0)
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "workers": views,
        "heartbeat_timeout_seconds": request.app.state.settings.worker_heartbeat_timeout_seconds,
        "note": (
            "Hardware values come from the last worker heartbeat snapshot. "
            "Unavailable metrics keep explicit availability states from the worker agent."
        ),
    }


@router.get("/workers/{worker_id}")
async def get_worker(worker_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.view", request_id)
    if denied:
        return denied
    views = {view["id"]: view for view in _worker_views(request)}
    worker = views.get(worker_id)
    if worker is None:
        return error_response(
            ErrorCode.WORKER_UNAVAILABLE,
            f"Unknown worker: {worker_id}",
            request_id,
            status_code=404,
            details={"worker_id": worker_id},
        )
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "worker": worker,
    }


@router.post("/workers/{worker_id}/maintenance")
async def mark_worker_maintenance(
    worker_id: str, body: MaintenanceBody, request: Request
):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.manage", request_id)
    if denied:
        return denied
    record = set_maintenance(
        request.app.state.workers, worker_id, maintenance=body.maintenance
    )
    if record is None:
        return error_response(
            ErrorCode.WORKER_UNAVAILABLE,
            f"Unknown worker: {worker_id}",
            request_id,
            status_code=404,
        )
    # Reflect maintenance on deployments assigned to this worker.
    for deployment in request.app.state.routes.deployments.values():
        if deployment.worker_id == worker_id:
            deployment.maintenance = body.maintenance
    _audit(
        request,
        {
            "event_type": (
                "worker.maintenance_enabled"
                if body.maintenance
                else "worker.maintenance_removed"
            ),
            "resource_type": "worker",
            "resource_id": worker_id,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "success",
            "metadata": {"maintenance": body.maintenance},
        },
    )
    views = {view["id"]: view for view in _worker_views(request)}
    return {"request_id": request_id, "worker": views.get(worker_id, record)}


@router.get("/hardware")
async def hardware_inventory(request: Request):
    """Hardware view assembled only from gateway-held worker heartbeat data."""

    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "hardware.view", request_id)
    if denied:
        return denied
    views = _worker_views(request)
    nodes = []
    for worker in views:
        hardware = worker.get("hardware_summary")
        source = "worker_heartbeat"
        if not worker.get("connected"):
            source = "last_heartbeat_snapshot_worker_disconnected"
        nodes.append(
            {
                "worker_id": worker.get("id"),
                "display_name": worker.get("display_name"),
                "connected": worker.get("connected"),
                "status": worker.get("status"),
                "maintenance": worker.get("maintenance_state"),
                "ready": worker.get("ready"),
                "last_heartbeat": worker.get("last_heartbeat"),
                "heartbeat_age_seconds": worker.get("heartbeat_age_seconds"),
                "agent_version": worker.get("agent_version"),
                "runtime_summary": worker.get("runtime_summary"),
                "last_error": worker.get("last_error"),
                "hardware": hardware,
                "source": source,
                "note": (
                    None
                    if hardware
                    else "No hardware snapshot has been reported by this worker yet."
                ),
            }
        )
    return {
        "request_id": get_request_id(request),
        "principal_subject": principal.subject,
        "nodes": nodes,
        "policy": {
            "console_must_not_call_workers_directly": True,
            "fabricated_metrics_forbidden": True,
            "source": "gateway_worker_registry_heartbeats",
        },
    }


@router.get("/operations/summary")
async def operations_summary(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.view", request_id)
    if denied:
        return denied
    settings = request.app.state.settings
    admission = settings.admission
    workers = _worker_views(request)
    routes = request.app.state.routes
    ledger = request.app.state.request_ledger
    providers = []
    for name in request.app.state.providers.names():
        provider = request.app.state.providers.get(name)
        try:
            health = await provider.health()
            providers.append(health.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001
            providers.append(
                {
                    "status": "unhealthy",
                    "provider": name,
                    "message": str(exc),
                }
            )

    active_requests = sum(
        deployment.active_requests for deployment in routes.deployments.values()
    )
    queue_depth = sum(
        deployment.queue_depth for deployment in routes.deployments.values()
    )
    deployment_capacity = []
    for deployment in routes.deployments.values():
        max_active = admission.max_active_requests_per_deployment
        max_queue = admission.max_queue_depth
        headroom_active = max(0, max_active - deployment.active_requests)
        headroom_queue = max(0, max_queue - deployment.queue_depth)
        deployment_capacity.append(
            {
                "deployment_id": deployment.id,
                "status": deployment.status.value,
                "active_requests": deployment.active_requests,
                "queue_depth": deployment.queue_depth,
                "max_active_requests": max_active,
                "max_queue_depth": max_queue,
                "headroom_active": headroom_active,
                "headroom_queue": headroom_queue,
                "saturated": headroom_active == 0 and headroom_queue == 0,
            }
        )
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "gateway": {
            "service": "yonder-gateway",
            "version": SERVICE_VERSION,
            "environment": settings.environment.value,
            "security_mode": settings.security.mode.value,
            "development_security_warning": settings.security.mode.value == "development",
            "status": "ok",
            "persistence_backend": getattr(
                request.app.state.persistence, "backend", "unknown"
            ),
        },
        "admission": {
            "max_active_requests_per_deployment": admission.max_active_requests_per_deployment,
            "max_queue_depth": admission.max_queue_depth,
            "queue_timeout_seconds": admission.queue_timeout_seconds,
            "inference_timeout_seconds": admission.inference_timeout_seconds,
            "max_prompt_chars": admission.max_prompt_chars,
            "max_output_tokens": admission.max_output_tokens,
        },
        "capacity": {
            "active_requests": active_requests,
            "queued_requests": queue_depth,
            "deployments": deployment_capacity,
            "any_saturated": any(item["saturated"] for item in deployment_capacity),
        },
        "workers": {
            "total": len(workers),
            "connected": sum(1 for worker in workers if worker.get("connected")),
            "disconnected": sum(1 for worker in workers if not worker.get("connected")),
            "maintenance": sum(1 for worker in workers if worker.get("maintenance_state")),
            "items": workers,
        },
        "routes": [
            {
                **route.model_dump(mode="json"),
                "lane": "experimental" if route.experimental else "production",
            }
            for route in routes.aliases.values()
        ],
        "deployments": [
            deployment.model_dump(mode="json")
            for deployment in routes.deployments.values()
        ],
        "active_requests": active_requests,
        "queued_requests": queue_depth,
        "recent_failures": ledger.list(failures_only=True)[:25],
        "recent_requests": ledger.list()[:25],
        "providers": providers,
        "recent_admin_events": request.app.state.event_store.list(limit=25),
    }


@router.post("/providers/{provider_name}/health-check")
async def retry_provider_health(provider_name: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.manage", request_id)
    if denied:
        return denied
    provider = request.app.state.providers.get(provider_name)
    if provider is None:
        return error_response(
            ErrorCode.PROVIDER_FAILURE,
            f"Unknown provider: {provider_name}",
            request_id,
            status_code=404,
        )
    try:
        health = await provider.health()
        result = "success"
        payload = health.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        result = "failed"
        payload = {"status": "unhealthy", "provider": provider_name, "message": str(exc)}
    _audit(
        request,
        {
            "event_type": "provider.health_check",
            "resource_type": "provider",
            "resource_id": provider_name,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": result,
            "metadata": payload,
        },
    )
    return {"request_id": request_id, "health": payload}


@router.get("/routes")
async def list_routes(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    routes = request.app.state.routes
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "aliases": [route.model_dump(mode="json") for route in routes.aliases.values()],
        "deployments": [
            deployment.model_dump(mode="json")
            for deployment in routes.deployments.values()
        ],
    }


@router.patch("/routes/{alias}")
async def update_alias(alias: str, body: AliasUpdateBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        route = request.app.state.lifecycle.set_alias(
            alias,
            body.deployment_id,
            principal_subject=principal.subject,
            request_id=request_id,
            enabled=body.enabled,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "alias": route.model_dump(mode="json"),
    }


@router.post("/routes/{alias}/enable")
async def enable_route(alias: str, body: RouteEnableBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.manage", request_id)
    if denied:
        return denied
    route = request.app.state.routes.get_alias(alias)
    if route is None:
        return error_response(
            ErrorCode.UNKNOWN_MODEL,
            f"Unknown alias: {alias}",
            request_id,
            details={"alias": alias},
        )
    previous = route.state.value
    route.state = RouteState.ENABLED if body.enabled else RouteState.DISABLED
    request.app.state.state_store.save_from(request.app.state.routes)
    _audit(
        request,
        {
            "event_type": "route.enabled" if body.enabled else "route.disabled",
            "resource_type": "alias",
            "resource_id": alias,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "success",
            "metadata": {"previous_state": previous, "new_state": route.state.value},
        },
    )
    return {"request_id": request_id, "alias": route.model_dump(mode="json")}


@router.get("/deployments")
async def list_deployments(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    routes = request.app.state.routes
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "deployments": [
            deployment.model_dump(mode="json")
            for deployment in routes.deployments.values()
        ],
    }


@router.get("/deployments/{deployment_id}")
async def get_deployment(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    deployment = request.app.state.routes.get_deployment(deployment_id)
    if deployment is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            f"Unknown deployment: {deployment_id}",
            request_id,
            status_code=404,
        )
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.get("/deployments/{deployment_id}/logs")
async def deployment_logs(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    deployment = request.app.state.routes.get_deployment(deployment_id)
    if deployment is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            f"Unknown deployment: {deployment_id}",
            request_id,
            status_code=404,
        )
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "deployment_id": deployment_id,
        "logs": list(deployment.logs),
    }


@router.post("/deployments/load")
async def load_deployment(body: LoadDeploymentBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = await request.app.state.lifecycle.load(
            deployment_id=body.deployment_id,
            model_definition_id=body.model_definition_id,
            worker_id=body.worker_id or request.app.state.settings.worker_id,
            placement_mode=body.placement_mode,
            gpu_indexes=body.gpu_indexes,
            context_length=body.context_length,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.post("/deployments/{deployment_id}/drain")
async def drain_deployment(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = await request.app.state.lifecycle.drain(
            deployment_id,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.post("/deployments/{deployment_id}/unload")
async def unload_deployment(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = await request.app.state.lifecycle.unload(
            deployment_id,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.post("/deployments/{deployment_id}/cancel")
async def cancel_queued_load(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = request.app.state.lifecycle.cancel_queued_load(
            deployment_id,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.post("/deployments/{deployment_id}/retry")
async def retry_failed_load(deployment_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = await request.app.state.lifecycle.retry_failed_load(
            deployment_id,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


class DeploymentLockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = True


@router.post("/deployments/{deployment_id}/lock")
async def set_deployment_lock(
    deployment_id: str, body: DeploymentLockBody, request: Request
):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        deployment = request.app.state.lifecycle.set_deployment_lock(
            deployment_id,
            locked=body.locked,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {
        "request_id": request_id,
        "deployment": deployment.model_dump(mode="json"),
    }


@router.get("/capacity/events")
async def list_capacity_events(request: Request, limit: int = 50):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    store = getattr(request.app.state, "capacity_events", None)
    events = store.list_events(limit=max(1, min(limit, 200))) if store else []
    settings = request.app.state.settings
    return {
        "request_id": request_id,
        "capacity": settings.capacity.model_dump(mode="json"),
        "events": events,
    }


@router.get("/models")
async def list_model_definitions(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    definitions = request.app.state.model_definitions
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "models": [item.model_dump(mode="json") for item in definitions.values()],
    }


@router.get("/model-folders/roots")
async def list_model_roots(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    roots = resolved_model_roots(request.app.state.settings.model_roots)
    return {
        "request_id": request_id,
        "roots": [{"path": str(root), "exists": root.is_dir()} for root in roots],
        "policy": {
            "paths_must_be_allowlisted": True,
            "environment_variable": "YONDER_MODEL_ROOTS",
            "runtime_auto_enable": False,
        },
    }


@router.post("/model-folders/scan")
async def scan_model_folder(body: ModelFolderBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.view", request_id)
    if denied:
        return denied
    try:
        path = resolve_allowed_path(body.path, request.app.state.settings.model_roots)
        candidates = await asyncio.to_thread(scan_model_path, path)
    except ModelDiscoveryError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            exc.message,
            request_id,
            details={"reason": exc.code},
        )
    return {
        "request_id": request_id,
        "path": str(path),
        "candidates": candidates,
        "recognized_count": sum(1 for item in candidates if item["recognized"]),
    }


@router.post("/model-folders/prepare")
async def prepare_model_folder(body: ModelFolderBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        path = resolve_allowed_path(body.path, request.app.state.settings.model_roots)
        candidate = await asyncio.to_thread(prepare_model_bundle, path, force=body.force)
    except ModelDiscoveryError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            exc.message,
            request_id,
            details={"reason": exc.code},
        )
    _audit(
        request,
        {
            "event_type": "model_folder.prepared",
            "resource_type": "model_bundle",
            "resource_id": candidate["id"],
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "success",
            "metadata": {
                "path": str(path),
                "modality": candidate["modality"],
                "runtime_candidate": candidate["runtime_candidate"],
                "runtime_enabled": False,
            },
        },
    )
    return {
        "request_id": request_id,
        "candidate": candidate,
        "next": (
            "Bundle files are checksummed. Record host evidence and approve a compatible "
            "provider before loading or enabling an alias."
        ),
    }


@router.post("/models/{model_id}/validate")
async def validate_model(model_id: str, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "models.manage", request_id)
    if denied:
        return denied
    try:
        result = request.app.state.lifecycle.validate_definition(model_id)
    except LifecycleError as exc:
        return error_response(exc.code, exc.message, request_id, details=exc.details)
    return {"request_id": request_id, **result}


@router.post("/test-prompt")
async def test_prompt(body: TestPromptBody, request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.manage", request_id)
    if denied:
        return denied
    chat_service = request.app.state.chat_service
    completion_request = ChatCompletionRequest(
        model=body.model_alias,
        messages=[ChatMessage(role="user", content=body.prompt)],
        stream=False,
    )
    try:
        chat_service.enforce_lane_access(body.model_alias, principal)
        response = await chat_service.complete(
            request_id=request_id, body=completion_request
        )
    except ChatServiceError as exc:
        _audit(
            request,
            {
                "event_type": "test_prompt.failed",
                "resource_type": "alias",
                "resource_id": body.model_alias,
                "principal_subject": principal.subject,
                "request_id": request_id,
                "result": "failed",
                "metadata": {"code": exc.code.value},
            },
        )
        return error_response(exc.code, exc.message, request_id, details=exc.details)

    _audit(
        request,
        {
            "event_type": "test_prompt.executed",
            "resource_type": "alias",
            "resource_id": body.model_alias,
            "principal_subject": principal.subject,
            "request_id": request_id,
            "result": "success",
        },
    )
    return {
        "request_id": request_id,
        "model_alias": body.model_alias,
        "response": response.model_dump(mode="json"),
    }


@router.get("/requests")
async def list_requests(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.view", request_id)
    if denied:
        return denied
    failures_only = request.query_params.get("failures") == "1"
    request_id_filter = request.query_params.get("request_id")
    ledger = request.app.state.request_ledger
    if request_id_filter:
        item = ledger.get(request_id_filter)
        return {
            "request_id": request_id,
            "principal_subject": principal.subject,
            "requests": [item] if item else [],
        }
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "requests": ledger.list(failures_only=failures_only),
    }


@router.get("/events")
async def list_events(request: Request):
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_capability(principal, "operations.view", request_id)
    if denied:
        return denied
    event_type = request.query_params.get("event_type")
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))
    events = request.app.state.event_store.list(limit=limit, event_type=event_type)
    return {
        "request_id": request_id,
        "principal_subject": principal.subject,
        "events": events,
        "filters": {"event_type": event_type, "limit": limit},
    }
