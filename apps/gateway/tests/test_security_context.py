"""Request context provider and middleware behavior."""

from __future__ import annotations

import pytest
from app.context import RequestContextMiddleware
from app.context.providers import (
    DevelopmentRequestContextProvider,
    TrustedIngressRequestContextProvider,
    build_request_context_provider,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from yonder_configuration.models import DevelopmentPrincipalConfig, SecurityConfig, SecurityMode


@pytest.mark.asyncio
async def test_development_provider_returns_configured_principal() -> None:
    provider = DevelopmentRequestContextProvider(
        DevelopmentPrincipalConfig(subject="dev-user", capabilities=("chat.use",))
    )

    class DummyRequest:
        headers = {"x-request-id": "upstream-1"}

    principal = await provider.resolve(DummyRequest())
    assert principal.subject == "dev-user"
    assert principal.has_capability("chat.use")
    assert principal.upstream_request_id == "upstream-1"


def test_trusted_ingress_fails_closed_without_subject() -> None:
    app = FastAPI()
    provider = TrustedIngressRequestContextProvider(
        SecurityConfig(mode=SecurityMode.TRUSTED_INGRESS)
    )
    app.add_middleware(RequestContextMiddleware, provider=provider, service_name="test")

    @app.get("/api/admin/workers")
    async def workers() -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    response = client.get("/api/admin/workers")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "SECURITY_CONTEXT_UNAVAILABLE"


def test_trusted_ingress_accepts_configured_headers() -> None:
    app = FastAPI()
    security = SecurityConfig(mode=SecurityMode.TRUSTED_INGRESS)
    provider = build_request_context_provider(security)
    app.add_middleware(RequestContextMiddleware, provider=provider, service_name="test")

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, object]:
        return {
            "subject": request.state.principal.subject,
            "capabilities": list(request.state.principal.capabilities),
            "roles": list(request.state.principal.roles),
            "groups": list(request.state.principal.groups),
            "attributes": dict(request.state.principal.attributes),
        }

    client = TestClient(app)
    response = client.get(
        "/whoami",
        headers={
            "X-Yonder-Trusted-Subject": "svc-yonder",
            "X-Yonder-Trusted-Capabilities": "operations.view,hardware.view",
            "X-Yonder-Trusted-Roles": "operator",
            "X-Yonder-Trusted-Groups": "ai-lab",
            "X-Yonder-Trusted-Attributes": "team=platform,env=lab",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "svc-yonder"
    assert "operations.view" in body["capabilities"]
    assert body["roles"] == ["operator"]
    assert body["groups"] == ["ai-lab"]
    assert body["attributes"]["team"] == "platform"


def test_trusted_ingress_rejects_blank_subject() -> None:
    app = FastAPI()
    provider = TrustedIngressRequestContextProvider(
        SecurityConfig(mode=SecurityMode.TRUSTED_INGRESS)
    )
    app.add_middleware(RequestContextMiddleware, provider=provider, service_name="test")

    @app.get("/secure")
    async def secure() -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    response = client.get(
        "/secure",
        headers={"X-Yonder-Trusted-Subject": "   "},
    )
    assert response.status_code == 401


def test_health_skips_principal_requirement() -> None:
    app = FastAPI()
    provider = TrustedIngressRequestContextProvider(
        SecurityConfig(mode=SecurityMode.TRUSTED_INGRESS)
    )
    app.add_middleware(RequestContextMiddleware, provider=provider, service_name="test")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Yonder-Request-Id" in response.headers
