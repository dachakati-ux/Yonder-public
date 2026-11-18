"""Gateway app smoke tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


def test_gateway_ops_endpoints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    # Import after env is set so create_app picks up development config.
    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["ready"] is True
    assert ready["development_security_warning"] is True
    assert client.get("/version").json()["service"] == "yonder-gateway"

    workers = client.get("/api/admin/workers")
    assert workers.status_code == 200
    body = workers.json()
    assert body["workers"] == []
    assert body["principal_subject"] == "yonder-local-developer"
