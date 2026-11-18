"""Worker inbound shared-secret authentication tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WORKER_SECRET = "unit-test-worker-secret"
TOKEN_HEADER = "X-Yonder-Worker-Token"


@pytest.fixture(autouse=True)
def worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(root / "config/environments/development.yaml"),
    )


def _client(monkeypatch: pytest.MonkeyPatch, secret: str | None) -> TestClient:
    if secret is not None:
        monkeypatch.setenv("YONDER_SECURITY__WORKER_SHARED_SECRET", secret)
    from app.main import create_app

    return TestClient(create_app())


def test_probe_routes_stay_open_with_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, WORKER_SECRET)
    for path in ("/health", "/ready", "/version", "/metrics"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)


def test_operational_routes_require_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, WORKER_SECRET)

    missing = client.get("/v1/lifecycle/status")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "worker_token_required"

    wrong = client.get(
        "/v1/lifecycle/status", headers={TOKEN_HEADER: "wrong-secret"}
    )
    assert wrong.status_code == 401

    valid = client.get(
        "/v1/lifecycle/status", headers={TOKEN_HEADER: WORKER_SECRET}
    )
    assert valid.status_code == 200, valid.text


def test_lifecycle_commands_require_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, WORKER_SECRET)
    denied = client.post(
        "/v1/lifecycle/load",
        json={
            "deployment_id": "dep-1",
            "model_definition_id": "model-1",
            "operation": "load",
        },
    )
    assert denied.status_code == 401

    allowed = client.post(
        "/v1/lifecycle/load",
        json={
            "deployment_id": "dep-1",
            "model_definition_id": "model-1",
            "operation": "load",
        },
        headers={TOKEN_HEADER: WORKER_SECRET},
    )
    assert allowed.status_code == 200, allowed.text


def test_no_secret_keeps_development_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YONDER_SECURITY__WORKER_SHARED_SECRET", raising=False)
    client = _client(monkeypatch, None)
    response = client.get("/v1/lifecycle/status")
    assert response.status_code == 200


def test_status_reports_honest_gateway_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connected_to_gateway must reflect real state, never hardcoded True."""

    client = _client(monkeypatch, WORKER_SECRET)
    response = client.get("/v1/status", headers={TOKEN_HEADER: WORKER_SECRET})
    assert response.status_code == 200
    body = response.json()
    # Lifespan did not run, so no registration has happened yet.
    assert body["connected_to_gateway"] is False
    assert body["last_heartbeat_at"] is None
