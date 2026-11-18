"""Worker shared-secret authentication and negative authorization coverage.

Covers the gateway side of the worker service token (registration under
trusted ingress) and asserts that capability checks on operator routes
actually deny minimal-capability principals — so a careless refactor cannot
silently drop authorization.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WORKER_SECRET = "unit-test-worker-secret"
TOKEN_HEADER = "X-Yonder-Worker-Token"
SUBJECT_HEADER = "X-Yonder-Trusted-Subject"
CAPABILITIES_HEADER = "X-Yonder-Trusted-Capabilities"


def _registration_payload(worker_id: str = "test-worker") -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "display_name": worker_id,
        "endpoint": "http://127.0.0.1:8090",
        "agent_version": "test",
        "ready": True,
        "capabilities": ["hardware.report", "heartbeat"],
    }


@pytest.fixture()
def trusted_ingress_client(
    gateway_env, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("YONDER_SECURITY__MODE", "trusted_ingress")
    monkeypatch.setenv("YONDER_SECURITY__WORKER_SHARED_SECRET", WORKER_SECRET)
    from app.main import create_app

    return TestClient(create_app())


def test_worker_registration_requires_token_under_trusted_ingress(
    trusted_ingress_client: TestClient,
) -> None:
    response = trusted_ingress_client.post(
        "/api/admin/workers/register", json=_registration_payload()
    )
    assert response.status_code == 401


def test_worker_registration_rejects_wrong_token(
    trusted_ingress_client: TestClient,
) -> None:
    response = trusted_ingress_client.post(
        "/api/admin/workers/register",
        json=_registration_payload(),
        headers={TOKEN_HEADER: "wrong-secret"},
    )
    assert response.status_code == 401


def test_worker_registration_accepts_valid_token(
    trusted_ingress_client: TestClient,
) -> None:
    response = trusted_ingress_client.post(
        "/api/admin/workers/register",
        json=_registration_payload(),
        headers={TOKEN_HEADER: WORKER_SECRET},
    )
    assert response.status_code == 200, response.text

    heartbeat = trusted_ingress_client.post(
        "/api/admin/workers/test-worker/heartbeat",
        json={
            "worker_id": "test-worker",
            "display_name": "test-worker",
            "endpoint": "http://127.0.0.1:8090",
            "agent_version": "test",
            "ready": True,
        },
        headers={TOKEN_HEADER: WORKER_SECRET},
    )
    assert heartbeat.status_code == 200, heartbeat.text


def test_worker_token_principal_is_scoped_to_workers_report(
    trusted_ingress_client: TestClient,
) -> None:
    """The synthesized worker principal must not reach operator routes."""

    for path in (
        "/api/admin/operations/summary",
        "/api/admin/workers",
        "/api/admin/deployments",
        "/api/admin/hardware",
    ):
        response = trusted_ingress_client.get(
            path, headers={TOKEN_HEADER: WORKER_SECRET}
        )
        assert response.status_code == 403, (path, response.status_code)
        assert response.json()["error"]["code"] == "PERMISSION_CONTEXT_REQUIRED"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/workers"),
        ("GET", "/api/admin/hardware"),
        ("GET", "/api/admin/operations/summary"),
        ("GET", "/api/admin/deployments"),
        ("POST", "/api/admin/routes/yonder-chat/enable"),
    ],
)
def test_minimal_capability_principal_is_denied(
    trusted_ingress_client: TestClient, method: str, path: str
) -> None:
    """A chat-only user must get 403 from every capability-gated route."""

    headers = {SUBJECT_HEADER: "limited-user", CAPABILITIES_HEADER: "chat.use"}
    body = {"enabled": True} if method == "POST" else None
    response = trusted_ingress_client.request(
        method, path, headers=headers, json=body
    )
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert response.json()["error"]["code"] == "PERMISSION_CONTEXT_REQUIRED"


def test_capability_grant_allows_access(trusted_ingress_client: TestClient) -> None:
    """Positive control: the 403s above are capability checks, not transport."""

    headers = {
        SUBJECT_HEADER: "ops-user",
        CAPABILITIES_HEADER: "operations.view",
    }
    response = trusted_ingress_client.get("/api/admin/workers", headers=headers)
    assert response.status_code == 200, response.text


def test_ready_reflects_accepting_requests(gateway_env) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True

    client.app.state.accepting_requests = False
    draining = client.get("/ready")
    assert draining.status_code == 503
    assert draining.json()["ready"] is False
