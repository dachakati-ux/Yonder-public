"""Add-on service handshakes are authenticated and reject untrusted endpoints."""
from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import ValidationError

from app.services.addon_service import (
    ServiceEndpoint,
    ServiceProbeError,
    probe_service,
)
from app.services.addon_service_registry import AddonServiceRegistry


def endpoint(**overrides):
    config = {
        "addon_id": "convyx",
        "expected_service_id": "convyx-service",
        "base_url": "https://convyx.example.org",
        "bearer_token": "replace-with-a-very-long-secret-token",
    }
    config.update(overrides)
    return ServiceEndpoint.model_validate(config)


@pytest.mark.parametrize(
    "url",
    [
        "http://convyx.example.org",
        "https://localhost",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://10.0.0.1",
        "https://convyx.example.org:8443",
        "https://user:password@convyx.example.org",
        "https://convyx.example.org/../admin",
        "https://convyx.example.org?url=bad",
        "file:///etc/passwd",
    ],
)
def test_invalid_service_endpoints_blocked(url):
    with pytest.raises(ValidationError):
        endpoint(base_url=url)


def test_endpoint_secret_excluded_from_serialization():
    value = endpoint()
    assert "bearer_token" not in value.model_dump()
    assert "secret" not in str(value)


@pytest.mark.asyncio
async def test_verified_handshake_with_stub_transport(monkeypatch):
    incoming = []

    def callback(request):
        incoming.append(request)
        return httpx.Response(
            200,
            json={
                "addon_id": "convyx",
                "service_id": "convyx-service",
                "api_version": 1,
                "version": "0.1.0",
                "capabilities": ["agent_orchestrator"],
                "health": "ready",
            },
        )

    transport = httpx.MockTransport(callback)
    original = httpx.AsyncClient

    def wrapped(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["verify"] is True
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", wrapped)
    response = await probe_service(endpoint())
    assert response["trusted"] is True
    assert response["service_id"] == "convyx-service"
    assert len(incoming) == 1
    assert incoming[0].url.path == "/addon/v1/capabilities"
    assert incoming[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_handshake_rejects_wrong_service(monkeypatch):
    original = httpx.AsyncClient

    def wrapped(**kwargs):
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "addon_id": "convyx",
                    "service_id": "wrong-service",
                    "api_version": 1,
                    "version": "0.1.0",
                    "capabilities": [],
                    "health": "ready",
                },
            )
        )
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", wrapped)
    with pytest.raises(ServiceProbeError, match="identity mismatch"):
        await probe_service(endpoint())


def test_config_file_permissions_and_secret_safety(tmp_path):
    path = tmp_path / "addon-services.json"
    data = {
        "schema_version": 1,
        "endpoints": [
            {
                "addon_id": "convyx",
                "expected_service_id": "convyx-service",
                "base_url": "https://convyx.example.org",
                "bearer_token": "replace-with-a-very-long-secret-token",
            }
        ],
    }
    path.write_text(json.dumps(data))
    registry = AddonServiceRegistry(path)
    if os.name == "posix":
        os.chmod(path, 0o644)
        with pytest.raises(ServiceProbeError, match="private"):
            registry.reload()
        os.chmod(path, 0o600)
    assert registry.reload() == {"status": "configured", "count": 1}
    assert registry.endpoints["convyx"].base_url == "https://convyx.example.org"


@pytest.mark.asyncio
async def test_uninstalled_service_fails_closed(tmp_path):
    registry = AddonServiceRegistry(tmp_path / "missing.json")
    assert registry.reload()["status"] == "not_configured"
    assert await registry.health("convyx", installed=False) == {
        "addon_id": "convyx", "status": "not_installed"
    }
