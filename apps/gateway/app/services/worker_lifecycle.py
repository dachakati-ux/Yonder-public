"""Gateway → worker lifecycle notifications."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_telemetry import log_event

logger = logging.getLogger("yonder.gateway.worker_lifecycle")


async def notify_worker_lifecycle(
    *,
    workers: dict[str, Any],
    worker_id: str,
    operation: str,
    deployment_id: str,
    model_definition_id: str | None = None,
    runtime_configuration: dict[str, Any] | None = None,
    gpu_assignments: list[int] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """POST lifecycle commands on the worker agent when an endpoint is known."""

    record = workers.get(worker_id)
    if record is None:
        return {"status": "skipped", "reason": "worker_not_registered"}
    endpoint = getattr(record, "endpoint", None) or (
        record.get("endpoint") if isinstance(record, dict) else None
    )
    if not endpoint:
        return {"status": "skipped", "reason": "worker_endpoint_missing"}

    # Loads wait for llama.cpp health on the worker (often >2m for first GGUF).
    if timeout_seconds is None:
        timeout_seconds = 600.0 if operation == "load" else 120.0

    url = f"{str(endpoint).rstrip('/')}/v1/lifecycle/{operation}"
    payload = {
        "deployment_id": deployment_id,
        "model_definition_id": model_definition_id,
        "operation": operation,
        "runtime_configuration": runtime_configuration or {},
        "gpu_assignments": list(gpu_assignments or []),
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds, headers=worker_auth_headers()
        ) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            log_event(
                logger,
                "worker.lifecycle.notify_failed",
                level=logging.WARNING,
                worker_id=worker_id,
                operation=operation,
                deployment_id=deployment_id,
                status_code=response.status_code,
            )
            return {
                "status": "error",
                "reason": "worker_rejected",
                "status_code": response.status_code,
                "body": response.text[:500],
            }
        body = response.json() if response.content else {}
        return {"status": "acknowledged", "worker_response": body}
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger,
            "worker.lifecycle.notify_failed",
            level=logging.WARNING,
            worker_id=worker_id,
            operation=operation,
            deployment_id=deployment_id,
            error=str(exc),
        )
        return {"status": "error", "reason": "worker_unreachable", "error": str(exc)}
