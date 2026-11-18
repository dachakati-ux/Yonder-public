"""Worker registration and heartbeat client."""

from __future__ import annotations

from typing import Any

import httpx
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.versioning import SERVICE_VERSION


class WorkerRegistrationRequired(Exception):
    """Gateway no longer knows this worker; re-registration is required."""


def _worker_payload(
    *,
    worker_id: str,
    display_name: str,
    endpoint: str,
    hardware: dict[str, Any],
    ready: bool,
    include_capabilities: bool,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "worker_id": worker_id,
        "display_name": display_name,
        "endpoint": endpoint,
        "agent_version": SERVICE_VERSION,
        "ready": ready,
        "hardware": hardware,
        "runtime": runtime
        or {
            "status": "not_reported",
            "message": "Runtime occupancy was not supplied",
            "deployments": [],
        },
    }
    if include_capabilities:
        payload["capabilities"] = [
            "hardware.report",
            "heartbeat",
            "lifecycle.stub",
            "generation.not_supported",
        ]
    return payload


async def register_with_gateway(
    *,
    gateway_url: str,
    worker_id: str,
    display_name: str,
    endpoint: str,
    hardware: dict[str, Any],
    ready: bool,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _worker_payload(
        worker_id=worker_id,
        display_name=display_name,
        endpoint=endpoint,
        hardware=hardware,
        ready=ready,
        include_capabilities=True,
        runtime=runtime,
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url.rstrip('/')}/api/admin/workers/register",
            json=payload,
            headers=worker_auth_headers(),
        )
        response.raise_for_status()
        return response.json()


async def send_heartbeat(
    *,
    gateway_url: str,
    worker_id: str,
    display_name: str,
    endpoint: str,
    hardware: dict[str, Any],
    ready: bool,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _worker_payload(
        worker_id=worker_id,
        display_name=display_name,
        endpoint=endpoint,
        hardware=hardware,
        ready=ready,
        include_capabilities=False,
        runtime=runtime,
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url.rstrip('/')}/api/admin/workers/{worker_id}/heartbeat",
            json=payload,
            headers=worker_auth_headers(),
        )
        if response.status_code == 404:
            raise WorkerRegistrationRequired(
                f"Gateway requires registration for worker {worker_id}"
            )
        response.raise_for_status()
        return response.json()


async def ensure_registered_heartbeat(
    *,
    gateway_url: str,
    worker_id: str,
    display_name: str,
    endpoint: str,
    hardware: dict[str, Any],
    ready: bool,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a heartbeat; on 404, re-register then heartbeat again."""

    try:
        return await send_heartbeat(
            gateway_url=gateway_url,
            worker_id=worker_id,
            display_name=display_name,
            endpoint=endpoint,
            hardware=hardware,
            ready=ready,
            runtime=runtime,
        )
    except WorkerRegistrationRequired:
        await register_with_gateway(
            gateway_url=gateway_url,
            worker_id=worker_id,
            display_name=display_name,
            endpoint=endpoint,
            hardware=hardware,
            ready=ready,
            runtime=runtime,
        )
        return await send_heartbeat(
            gateway_url=gateway_url,
            worker_id=worker_id,
            display_name=display_name,
            endpoint=endpoint,
            hardware=hardware,
            ready=ready,
            runtime=runtime,
        )


async def disconnect_from_gateway(*, gateway_url: str, worker_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{gateway_url.rstrip('/')}/api/admin/workers/{worker_id}/disconnect",
            headers=worker_auth_headers(),
        )
        response.raise_for_status()
        return response.json()
