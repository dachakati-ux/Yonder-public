"""Phase 6 operations and hardware aggregation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def _register_worker(client: TestClient, *, worker_id: str = "local-dev-worker") -> None:
    response = client.post(
        "/api/admin/workers/register",
        json={
            "worker_id": worker_id,
            "display_name": "Local worker",
            "endpoint": "http://127.0.0.1:8090",
            "agent_version": "0.1.0",
            "ready": True,
            "hardware": {
                "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "gpu_discovery_status": "not_supported",
                "gpu_discovery_message": "nvidia-smi not found",
                "notes": ["test snapshot"],
                "host": {
                    "hostname": {
                        "availability": "available",
                        "value": "test-host",
                        "unit": None,
                        "message": None,
                    },
                    "cpu_model": {
                        "availability": "available",
                        "value": "Test CPU",
                        "unit": None,
                        "message": None,
                    },
                    "nvidia_driver_version": {
                        "availability": "not_supported",
                        "value": None,
                        "unit": None,
                        "message": "nvidia-smi not found",
                    },
                },
                "gpus": [],
            },
            "runtime": {"status": "skeleton"},
        },
    )
    assert response.status_code == 200


def test_hardware_endpoint_uses_heartbeat_snapshot(client: TestClient) -> None:
    _register_worker(client)
    response = client.get("/api/admin/hardware")
    assert response.status_code == 200
    body = response.json()
    assert body["policy"]["console_must_not_call_workers_directly"] is True
    assert len(body["nodes"]) == 1
    node = body["nodes"][0]
    assert node["connected"] is True
    assert node["hardware"]["gpu_discovery_status"] == "not_supported"
    assert node["hardware"]["gpus"] == []
    assert node["source"] == "worker_heartbeat"


def test_worker_heartbeat_expiry_marks_disconnected(client: TestClient) -> None:
    _register_worker(client)
    stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    client.app.state.workers["local-dev-worker"]["last_heartbeat"] = stale
    response = client.get("/api/admin/workers")
    assert response.status_code == 200
    worker = response.json()["workers"][0]
    assert worker["status"] == "disconnected"
    assert worker["connected"] is False
    assert worker["connectivity_reason"] == "heartbeat_expired"

    hardware = client.get("/api/admin/hardware").json()["nodes"][0]
    assert hardware["connected"] is False
    assert "disconnected" in hardware["source"]


def test_operations_summary_and_request_ledger(client: TestClient) -> None:
    _register_worker(client)
    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "ops"}],
            "stream": False,
        },
    )
    assert chat.status_code == 200
    request_id = chat.headers["X-Yonder-Request-Id"]

    failed = client.post(
        "/v1/chat/completions",
        json={
            "model": "missing-model",
            "messages": [{"role": "user", "content": "x"}],
            "stream": False,
        },
    )
    assert failed.status_code == 404
    fail_id = failed.json()["error"]["request_id"]

    summary = client.get("/api/admin/operations/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["gateway"]["status"] == "ok"
    assert body["workers"]["connected"] == 1
    assert any(item["request_id"] == request_id for item in body["recent_requests"])
    assert any(item["request_id"] == fail_id for item in body["recent_failures"])

    lookup = client.get(f"/api/admin/requests?request_id={fail_id}")
    assert lookup.status_code == 200
    assert lookup.json()["requests"][0]["error_code"] == "UNKNOWN_MODEL"


def test_maintenance_and_provider_health_create_events(client: TestClient) -> None:
    _register_worker(client)
    maintained = client.post(
        "/api/admin/workers/local-dev-worker/maintenance",
        json={"maintenance": True},
    )
    assert maintained.status_code == 200
    assert maintained.json()["worker"]["maintenance_state"] is True

    health = client.post("/api/admin/providers/mock/health-check")
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "healthy"

    events = client.get("/api/admin/events").json()["events"]
    assert any(event["event_type"] == "worker.maintenance_enabled" for event in events)
    assert any(event["event_type"] == "provider.health_check" for event in events)


def test_heartbeat_expiry_helper_unit() -> None:
    from app.services.workers import apply_heartbeat_expiry

    now = datetime.now(UTC)
    workers = {
        "w1": {
            "id": "w1",
            "last_heartbeat": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
            "ready": True,
        },
        "w2": {
            "id": "w2",
            "last_heartbeat": (now - timedelta(seconds=90)).isoformat().replace("+00:00", "Z"),
            "ready": True,
        },
    }
    views = apply_heartbeat_expiry(workers, timeout_seconds=30, now=now)
    by_id = {view["id"]: view for view in views}
    assert by_id["w1"]["connected"] is True
    assert by_id["w2"]["connected"] is False
    assert by_id["w2"]["status"] == "disconnected"
