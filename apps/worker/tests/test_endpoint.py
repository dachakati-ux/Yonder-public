"""Tests for worker advertised endpoint resolution."""

from __future__ import annotations

from app.registration.endpoint import resolve_worker_endpoint
from yonder_configuration.models import ServiceBindConfig, YonderSettings


def test_resolve_worker_endpoint_prefers_public() -> None:
    settings = YonderSettings(
        worker=ServiceBindConfig(host="0.0.0.0", port=8090),
        worker_public_endpoint="http://192.168.10.21:8090",
    )
    assert resolve_worker_endpoint(settings) == "http://192.168.10.21:8090"


def test_resolve_worker_endpoint_falls_back_to_bind() -> None:
    settings = YonderSettings(
        worker=ServiceBindConfig(host="127.0.0.1", port=8090),
        worker_public_endpoint=None,
    )
    assert resolve_worker_endpoint(settings) == "http://127.0.0.1:8090"
