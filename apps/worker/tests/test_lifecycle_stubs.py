"""Worker lifecycle stub endpoint tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(root / "config/environments/development.yaml"),
    )


def test_lifecycle_stubs_acknowledge_commands() -> None:
    from app.main import create_app

    client = TestClient(create_app())
    load = client.post(
        "/v1/lifecycle/load",
        json={
            "deployment_id": "dep-1",
            "model_definition_id": "model-1",
            "operation": "load",
        },
    )
    assert load.status_code == 200
    assert load.json()["accepted"] is True
    assert load.json()["deployment"]["status"] == "ready_stub"

    drain = client.post(
        "/v1/lifecycle/drain",
        json={"deployment_id": "dep-1", "operation": "drain"},
    )
    assert drain.status_code == 200
    assert drain.json()["deployment"]["status"] == "draining_stub"

    unload = client.post(
        "/v1/lifecycle/unload",
        json={"deployment_id": "dep-1", "operation": "unload"},
    )
    assert unload.status_code == 200
    assert unload.json()["deployment"]["status"] == "stopped_stub"

    status = client.get("/v1/lifecycle/status")
    assert status.status_code == 200
    assert len(status.json()["deployments"]) == 1
