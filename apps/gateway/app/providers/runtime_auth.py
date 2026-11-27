"""Restrict worker credentials to deployment-scoped worker proxy requests."""

from __future__ import annotations

from urllib.parse import urlsplit

from yonder_configuration.worker_auth import worker_auth_headers


def runtime_proxy_headers(endpoint: str, deployment_id: str | None) -> dict[str, str]:
    """Direct model servers must never receive the Gateway/worker service secret."""
    if not deployment_id:
        return {}
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != f"/v1/runtime/{deployment_id}"
    ):
        return {}
    return worker_auth_headers()
