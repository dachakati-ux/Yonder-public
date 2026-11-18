"""Helpers for worker advertised endpoints."""

from __future__ import annotations

from yonder_configuration.models import YonderSettings


def resolve_worker_endpoint(settings: YonderSettings) -> str:
    """Return the Gateway-reachable worker URL.

    Prefer ``worker_public_endpoint`` (required for multi-host when the process
    binds to ``0.0.0.0``). Otherwise fall back to bind host:port.
    """
    if settings.worker_public_endpoint:
        return settings.worker_public_endpoint.rstrip("/")
    return f"http://{settings.worker.host}:{settings.worker.port}"
