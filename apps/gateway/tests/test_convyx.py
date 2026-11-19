"""Tests for optional Convyx agent-collaboration adapter (M07)."""

from __future__ import annotations

from types import MappingProxyType

import httpx
import pytest
from app.api import convyx as convyx_api
from app.context import RequestContextMiddleware
from app.services.convyx import ConvyxAdapter, ConvyxConfig, ConvyxError, validate_convyx_url
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yonder_contracts.principal import RequestPrincipal


class _StaticProvider:
    def __init__(self, principal: RequestPrincipal) -> None:
        self._p = principal

    async def resolve(self, _request) -> RequestPrincipal:
        return self._p


def _principal(*, capabilities: tuple[str, ...]) -> RequestPrincipal:
    return RequestPrincipal(
        subject="dev-1",
        principal_type="user",
        capabilities=capabilities,
        attributes=MappingProxyType({"tenant_id": "school-1"}),
    )


def test_validate_convyx_url_blocks_insecure_or_private() -> None:
    assert validate_convyx_url("https://convyx.example.org") == "https://convyx.example.org"
    for bad in ("", "http://convyx.example.org", "https://user:pass@convyx.example.org", "https://127.0.0.1"):
        with pytest.raises(ConvyxError):
            validate_convyx_url(bad, allow_insecure_http=False)


def test_convyx_unconfigured_degrades_gracefully() -> None:
    app = FastAPI()
    app.state.convyx_adapter = ConvyxAdapter(ConvyxConfig(enabled=False))
    app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(_principal(capabilities=("coder.use",))),
        service_name="test",
    )
    app.include_router(convyx_api.router)
    client = TestClient(app)

    status = client.get("/v1/convyx/status")
    assert status.status_code == 200
    assert status.json()["availability"] == "not_configured"

    handoff = client.post(
        "/v1/convyx/handoffs",
        json={"task_id": "task-1", "summary": "Review PR", "target_agent": "reviewer"},
    )
    assert handoff.status_code == 503


def test_convyx_authenticated_handoff_and_capability_enforcement() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer convyx-tok-123"
        seen.append({"path": request.url.path, "subject": request.headers.get("X-Yonder-Subject")})
        if request.url.path == "/convyx/v1/capabilities":
            return httpx.Response(200, json={"service": "convyx", "agents": ["reviewer"]})
        if request.url.path == "/convyx/v1/handoffs":
            return httpx.Response(200, json={"handoff_id": "ho-1", "status": "queued"})
        if request.url.path == "/convyx/v1/handoffs/ho-1":
            return httpx.Response(200, json={"handoff_id": "ho-1", "status": "completed"})
        return httpx.Response(404)

    adapter = ConvyxAdapter(
        ConvyxConfig(
            enabled=True,
            base_url="https://convyx.example.org",
            service_token="convyx-tok-123",
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    no_cap_app = FastAPI()
    no_cap_app.state.convyx_adapter = adapter
    no_cap_app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(_principal(capabilities=("chat.use",))),
        service_name="test",
    )
    no_cap_app.include_router(convyx_api.router)
    assert TestClient(no_cap_app).get("/v1/convyx/status").status_code == 403

    app = FastAPI()
    app.state.convyx_adapter = adapter
    app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(_principal(capabilities=("coder.use",))),
        service_name="test",
    )
    app.include_router(convyx_api.router)
    client = TestClient(app)

    assert client.get("/v1/convyx/status").json()["availability"] == "ready"
    res = client.post(
        "/v1/convyx/handoffs",
        json={"task_id": "task-1", "summary": "Review PR", "target_agent": "reviewer"},
    )
    assert res.status_code == 200
    assert res.json()["handoff_id"] == "ho-1"
    assert client.get("/v1/convyx/handoffs/ho-1").json()["status"] == "completed"
    assert seen[1]["subject"] == "dev-1"
