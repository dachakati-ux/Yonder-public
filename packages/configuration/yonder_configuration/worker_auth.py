"""Shared-secret service authentication for gateway <-> worker calls.

This is infrastructure trust between Yonder services, not corporate user
authentication (which stays upstream per the security boundary). When
``security.worker_shared_secret`` is configured, every gateway -> worker and
worker -> gateway HTTP call carries the secret in
``security.worker_token_header``, and the receiving service verifies it with
a constant-time comparison. Production configuration validation requires the
secret to be set; development and lab may run without it.

``configure_worker_auth`` is called once per process at app startup so call
sites that only hold narrow config objects (providers, registration client)
can attach headers without threading settings through every constructor.
"""

from __future__ import annotations

import hmac

from yonder_configuration.models import SecurityConfig

_state: dict[str, str] = {}


def configure_worker_auth(security: SecurityConfig) -> None:
    """Record the process-wide worker auth secret and header name."""

    _state.clear()
    secret = (security.worker_shared_secret or "").strip()
    if secret:
        _state["secret"] = secret
        _state["header"] = security.worker_token_header


def worker_auth_headers() -> dict[str, str]:
    """Headers to attach to gateway<->worker service calls (may be empty)."""

    if "secret" not in _state:
        return {}
    return {_state["header"]: _state["secret"]}


def worker_auth_enabled() -> bool:
    return "secret" in _state


def worker_token_matches(security: SecurityConfig, presented: str | None) -> bool:
    """Constant-time comparison of a presented worker token."""

    secret = (security.worker_shared_secret or "").strip()
    if not secret or not presented:
        return False
    return hmac.compare_digest(secret.encode("utf-8"), presented.encode("utf-8"))
