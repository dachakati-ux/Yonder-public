"""Integration tests spanning gateway subsystems."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("YONDER_CONFIG_FILE", str(ROOT / "config/environments/development.yaml"))
    monkeypatch.setenv("YONDER_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{tmp_path / 'integ.db'}")
    import sys

    sys.path.insert(0, str(ROOT / "apps/gateway"))
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_gateway_worker_register_heartbeat_disconnect(client: TestClient) -> None:
    registered = client.post(
        "/api/admin/workers/register",
        json={
            "worker_id": "integ-worker",
            "display_name": "Integration worker",
            "endpoint": "http://127.0.0.1:8090",
            "agent_version": "0.1.0",
            "ready": True,
            "hardware": {
                "gpu_discovery_status": "not_supported",
                "gpus": [],
                "host": {},
                "notes": [],
            },
        },
    )
    assert registered.status_code == 200

    heartbeat = client.post(
        "/api/admin/workers/integ-worker/heartbeat",
        json={
            "worker_id": "integ-worker",
            "display_name": "Integration worker",
            "endpoint": "http://127.0.0.1:8090",
            "agent_version": "0.1.0",
            "ready": True,
        },
    )
    assert heartbeat.status_code == 200

    hardware = client.get("/api/admin/hardware")
    assert hardware.status_code == 200
    assert hardware.json()["nodes"][0]["worker_id"] == "integ-worker"

    disconnected = client.post("/api/admin/workers/integ-worker/disconnect")
    assert disconnected.status_code == 200
    workers = client.get("/api/admin/workers").json()["workers"]
    assert workers[0]["status"] == "disconnected"


def test_chat_stream_and_failure_correlation(client: TestClient) -> None:
    ok = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert ok.status_code == 200
    request_id = ok.headers["X-Yonder-Request-Id"]

    failed = client.post(
        "/v1/chat/completions",
        json={
            "model": "nope",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert failed.status_code == 404
    fail_id = failed.json()["error"]["request_id"]

    summary = client.get("/api/admin/operations/summary").json()
    assert any(item["request_id"] == request_id for item in summary["recent_requests"])
    assert any(item["request_id"] == fail_id for item in summary["recent_failures"])


def test_alias_repoint_and_lifecycle(client: TestClient) -> None:
    client.post("/api/admin/deployments/yonder-chat-mock-primary/unload")
    loaded = client.post(
        "/api/admin/deployments/load",
        json={"model_definition_id": "yonder-chat-small"},
    )
    assert loaded.status_code == 200
    deployment_id = loaded.json()["deployment"]["id"]
    updated = client.patch(
        "/api/admin/routes/yonder-chat",
        json={"deployment_id": deployment_id, "enabled": True},
    )
    assert updated.status_code == 200
    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "alias ok"}],
            "stream": False,
        },
    )
    assert chat.status_code == 200
