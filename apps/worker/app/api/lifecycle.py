"""Worker-local lifecycle endpoints (stubs or real llama.cpp manager)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.registration.endpoint import resolve_worker_endpoint
from app.runtime.occupancy import union_occupied_gpus

router = APIRouter(prefix="/v1/lifecycle", tags=["lifecycle"])


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    model_definition_id: str | None = None
    operation: str | None = None
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    gpu_assignments: list[int] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _deployments(request: Request) -> dict[str, dict[str, Any]]:
    if not hasattr(request.app.state, "lifecycle_deployments"):
        request.app.state.lifecycle_deployments = {}
    return request.app.state.lifecycle_deployments


def _manager(request: Request):
    return getattr(request.app.state, "llama_runtime", None)


def _manager_for(body: LifecycleCommand, request: Request):
    provider = str(body.runtime_configuration.get("provider") or "")
    if provider == "speech":
        return getattr(request.app.state, "speech_runtime", None)
    if provider == "diffusers":
        return getattr(request.app.state, "diffusers_runtime", None)
    speech = getattr(request.app.state, "speech_runtime", None)
    if speech is not None and any(
        item.get("deployment_id") == body.deployment_id
        for item in speech.list_deployments()
    ):
        return speech
    diffusers = getattr(request.app.state, "diffusers_runtime", None)
    if diffusers is not None and any(
        item.get("deployment_id") == body.deployment_id
        for item in diffusers.list_deployments()
    ):
        return diffusers
    return _manager(request)


def _manager_active(manager: Any) -> bool:
    return bool(
        manager is not None
        and (getattr(manager, "enabled", False) or getattr(manager, "dry_run", False))
    )


def _runtime_proxy_url(request: Request, deployment_id: str) -> str:
    worker_endpoint = resolve_worker_endpoint(request.app.state.settings)
    return f"{worker_endpoint}/v1/runtime/{deployment_id}"


@router.get("/status")
async def lifecycle_status(request: Request) -> dict[str, object]:
    manager = _manager(request)
    diffusers = getattr(request.app.state, "diffusers_runtime", None)
    speech = getattr(request.app.state, "speech_runtime", None)
    if (
        _manager_active(manager)
        or _manager_active(diffusers)
        or _manager_active(speech)
    ):
        deployments = []
        if _manager_active(manager):
            deployments.extend(
                {**item, "runtime_family": "llama_cpp"}
                for item in manager.list_deployments()
            )
        if _manager_active(diffusers):
            deployments.extend(
                {**item, "runtime_family": "diffusers"}
                for item in diffusers.list_deployments()
            )
        if _manager_active(speech):
            deployments.extend(
                {**item, "runtime_family": "speech"}
                for item in speech.list_deployments()
            )
        # Deployments acknowledged by the stub path (e.g. llama runtime
        # disabled while speech dry_run is active) must stay visible.
        managed_ids = {item.get("deployment_id") for item in deployments}
        deployments.extend(
            {**item, "runtime_family": "stub"}
            for item in _deployments(request).values()
            if item.get("deployment_id") not in managed_ids
        )
        return {
            "worker_id": request.app.state.settings.worker_id,
            "runtime": "unified",
            "deployments": deployments,
            "note": "Worker-managed llama.cpp, Diffusers, and speech occupancy.",
        }
    deployments = _deployments(request)
    return {
        "worker_id": request.app.state.settings.worker_id,
        "runtime": "skeleton",
        "deployments": list(deployments.values()),
        "note": (
            "Lifecycle stubs acknowledge gateway commands when "
            "llama_cpp_runtime.enabled is false."
        ),
    }


@router.post("/load")
async def lifecycle_load(body: LifecycleCommand, request: Request) -> dict[str, object]:
    manager = _manager_for(body, request)
    if _manager_active(manager):
        try:
            # Attach source.path from gateway when provided under nested keys.
            runtime_cfg = dict(body.runtime_configuration)
            if "source_path" not in runtime_cfg and "model_path" not in runtime_cfg:
                # Gateway may only send runtime.*; model path must be supplied by
                # the controller via model definition — for now accept explicit keys.
                pass
            peer_occupied = union_occupied_gpus(
                getattr(request.app.state, "llama_runtime", None),
                getattr(request.app.state, "diffusers_runtime", None),
                exclude=body.deployment_id,
                max_lanes_per_gpu=int(
                    getattr(
                        getattr(request.app.state.settings, "llama_cpp_runtime", None),
                        "max_lanes_per_gpu",
                        3,
                    )
                    or getattr(
                        getattr(request.app.state.settings, "diffusers_runtime", None),
                        "max_lanes_per_gpu",
                        3,
                    )
                    or 3
                ),
            )
            record = manager.load(
                deployment_id=body.deployment_id,
                model_definition_id=body.model_definition_id,
                runtime_configuration=runtime_cfg,
                gpu_assignments=list(body.gpu_assignments),
                maintenance=bool(
                    getattr(request.app.state.settings, "maintenance", False)
                ),
                already_occupied=peer_occupied,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Keep llama.cpp loopback-only. Gateway traffic returns through the
        # worker's fixed control endpoint, which proxies to this deployment.
        if manager is _manager(request):
            base_url = _runtime_proxy_url(request, body.deployment_id)
        else:
            # In-process Diffusers/speech loads have no sidecar port. Advertise
            # the worker control endpoint so Gateway can reach generation/speech.
            base_url = resolve_worker_endpoint(request.app.state.settings)
        advertised = {**record, "base_url": base_url}
        return {
            "accepted": True,
            "deployment": advertised,
            "base_url": base_url,
            "load_duration_ms": record.get("load_duration_ms"),
            "load_started_at": record.get("load_started_at"),
            "load_finished_at": record.get("load_finished_at"),
            "artifact_bytes": record.get("artifact_bytes"),
        }

    deployments = _deployments(request)
    port = 8100 + (abs(hash(body.deployment_id)) % 1000)
    base_url = f"http://127.0.0.1:{port}"
    record = {
        "deployment_id": body.deployment_id,
        "model_definition_id": body.model_definition_id,
        "status": "ready_stub",
        "operation": "load",
        "updated_at": _now(),
        "runtime_configuration": body.runtime_configuration,
        "gpu_assignments": list(body.gpu_assignments),
        "base_url": base_url,
        "message": "Acknowledged load stub; no GPU runtime attached yet.",
    }
    deployments[body.deployment_id] = record
    return {"accepted": True, "deployment": record, "base_url": base_url}


@router.post("/drain")
async def lifecycle_drain(body: LifecycleCommand, request: Request) -> dict[str, object]:
    manager = _manager_for(body, request)
    if _manager_active(manager):
        try:
            record = manager.drain(body.deployment_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"accepted": True, "deployment": record}

    deployments = _deployments(request)
    existing = deployments.get(body.deployment_id, {})
    record = {
        **existing,
        "deployment_id": body.deployment_id,
        "model_definition_id": body.model_definition_id
        or existing.get("model_definition_id"),
        "status": "draining_stub",
        "operation": "drain",
        "updated_at": _now(),
        "message": "Acknowledged drain stub.",
    }
    deployments[body.deployment_id] = record
    return {"accepted": True, "deployment": record}


@router.post("/unload")
async def lifecycle_unload(body: LifecycleCommand, request: Request) -> dict[str, object]:
    manager = _manager_for(body, request)
    if _manager_active(manager):
        record = manager.unload(body.deployment_id)
        return {"accepted": True, "deployment": record}

    deployments = _deployments(request)
    existing = deployments.get(body.deployment_id, {})
    record = {
        **existing,
        "deployment_id": body.deployment_id,
        "model_definition_id": body.model_definition_id
        or existing.get("model_definition_id"),
        "status": "stopped_stub",
        "operation": "unload",
        "updated_at": _now(),
        "message": "Acknowledged unload stub; no GPU runtime attached yet.",
    }
    deployments[body.deployment_id] = record
    return {"accepted": True, "deployment": record}
