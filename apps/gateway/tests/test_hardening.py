"""Phase 7 hardening unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.persistence import JsonFileStateStore
from app.routing import (
    DeploymentRecord,
    DeploymentState,
    ModelAliasRoute,
    RouteRegistry,
    RouteState,
)
from fastapi.testclient import TestClient
from yonder_contracts.errors import ErrorCode
from yonder_telemetry import redact_log_fields

ROOT = Path(__file__).resolve().parents[3]


def test_state_store_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonFileStateStore(path)
    routes = RouteRegistry()
    routes.upsert_deployment(
        DeploymentRecord(
            id="dep-1",
            model_definition_id="yonder-chat-small",
            worker_id="w1",
            provider="mock",
            status=DeploymentState.STOPPED,
            active_requests=3,
            queue_depth=2,
        )
    )
    routes.upsert_alias(
        ModelAliasRoute(
            alias="yonder-chat",
            deployment_id="dep-1",
            state=RouteState.UNAVAILABLE,
        )
    )
    store.save_from(routes)

    restored = RouteRegistry()
    store.load_into(restored)
    deployment = restored.get_deployment("dep-1")
    assert deployment is not None
    assert deployment.status == DeploymentState.STOPPED
    assert deployment.active_requests == 0
    assert deployment.queue_depth == 0
    alias = restored.get_alias("yonder-chat")
    assert alias is not None
    assert alias.deployment_id == "dep-1"
    assert alias.state == RouteState.UNAVAILABLE


def test_gateway_restart_preserves_unloaded_state(
    gateway_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        unloaded = client.post("/api/admin/deployments/yonder-chat-mock-primary/unload")
        assert unloaded.status_code == 200
        assert unloaded.json()["deployment"]["status"] == "stopped"

    # Simulate process restart with the same durable state file.
    from app.main import create_app as create_app_again

    with TestClient(create_app_again()) as client2:
        deployment = client2.get("/api/admin/deployments/yonder-chat-mock-primary")
        assert deployment.status_code == 200
        assert deployment.json()["deployment"]["status"] == "stopped"
        chat = client2.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert chat.status_code == 503


def test_capacity_exceeded_returns_429(gateway_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YONDER_ADMISSION__MAX_ACTIVE_REQUESTS_PER_DEPLOYMENT", "1")
    monkeypatch.setenv("YONDER_ADMISSION__MAX_QUEUE_DEPTH", "0")
    from app.main import create_app

    with TestClient(create_app()) as client:
        deployment = client.app.state.routes.deployments["yonder-chat-mock-primary"]
        deployment.active_requests = 1
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == ErrorCode.CAPACITY_EXCEEDED.value


def test_log_redaction_blocks_secrets_and_prompts_by_default() -> None:
    redacted = redact_log_fields(
        {
            "database_url": "postgresql://user:secret@localhost/db",
            "prompt": "do not log me",
            "request_id": "req_1",
        }
    )
    assert redacted["database_url"] == "***REDACTED***"
    assert redacted["prompt"] == "***REDACTED***"
    assert redacted["request_id"] == "req_1"


def test_worker_disconnect_marks_disconnected(gateway_env: Path) -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        client.post(
            "/api/admin/workers/register",
            json={
                "worker_id": "w1",
                "display_name": "W1",
                "endpoint": "http://127.0.0.1:8090",
                "agent_version": "0.1.0",
                "ready": True,
            },
        )
        disconnected = client.post("/api/admin/workers/w1/disconnect")
        assert disconnected.status_code == 200
        workers = client.get("/api/admin/workers").json()["workers"]
        assert workers[0]["status"] == "disconnected"
        assert workers[0]["connected"] is False


def test_production_development_security_still_refused() -> None:
    from yonder_configuration.models import YonderSettings
    from yonder_configuration.validation import ConfigurationError, validate_settings

    with pytest.raises(ConfigurationError):
        validate_settings(
            YonderSettings(environment="production", security={"mode": "development"})
        )
