"""Deployment lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from yonder_contracts.errors import ErrorCode

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def test_list_models_includes_manifest_definition(client: TestClient) -> None:
    response = client.get("/api/admin/models")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["models"]]
    assert "yonder-chat-small" in ids


def test_validate_model_definition(client: TestClient) -> None:
    response = client.post("/api/admin/models/yonder-chat-small/validate")
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_unload_then_load_cycle(client: TestClient) -> None:
    deployment_id = "yonder-chat-mock-primary"
    unloaded = client.post(f"/api/admin/deployments/{deployment_id}/unload")
    assert unloaded.status_code == 200
    assert unloaded.json()["deployment"]["status"] == "stopped"

    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert chat.status_code == 503

    loaded = client.post(
        "/api/admin/deployments/load",
        json={"deployment_id": deployment_id},
    )
    assert loaded.status_code == 200
    assert loaded.json()["deployment"]["status"] == "ready"

    chat_ok = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert chat_ok.status_code == 200


def test_conflicting_load_while_ready_returns_409(client: TestClient) -> None:
    response = client.post(
        "/api/admin/deployments/load",
        json={"deployment_id": "yonder-chat-mock-primary"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.CONFLICTING_OPERATION.value


def test_drain_rejects_new_chat_then_unload(client: TestClient) -> None:
    deployment_id = "yonder-chat-mock-primary"
    drained = client.post(f"/api/admin/deployments/{deployment_id}/drain")
    assert drained.status_code == 200
    assert drained.json()["deployment"]["status"] == "draining"

    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert chat.status_code == 503

    unloaded = client.post(f"/api/admin/deployments/{deployment_id}/unload")
    assert unloaded.status_code == 200
    assert unloaded.json()["deployment"]["status"] == "stopped"


def test_alias_can_be_repointed(client: TestClient) -> None:
    client.post("/api/admin/deployments/yonder-chat-mock-primary/unload")
    secondary = client.post(
        "/api/admin/deployments/load",
        json={"model_definition_id": "yonder-chat-small"},
    )
    assert secondary.status_code == 200
    secondary_id = secondary.json()["deployment"]["id"]
    assert secondary_id == "yonder-chat-small-primary"

    updated = client.patch(
        "/api/admin/routes/yonder-chat",
        json={"deployment_id": secondary_id, "enabled": True},
    )
    assert updated.status_code == 200
    assert updated.json()["alias"]["deployment_id"] == secondary_id

    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "still works"}],
            "stream": False,
        },
    )
    assert chat.status_code == 200
    assert chat.json()["model"] == "yonder-chat"


def test_test_prompt_logs_and_events(client: TestClient) -> None:
    prompt = client.post(
        "/api/admin/test-prompt",
        json={"model_alias": "yonder-chat", "prompt": "ping"},
    )
    assert prompt.status_code == 200
    content = prompt.json()["response"]["choices"][0]["message"]["content"]
    assert "mock response" in content.lower()

    logs = client.get("/api/admin/deployments/yonder-chat-mock-primary/logs")
    assert logs.status_code == 200
    assert isinstance(logs.json()["logs"], list)

    events = client.get("/api/admin/events")
    assert any(
        event.get("event_type") == "test_prompt.executed"
        for event in events.json()["events"]
    )


def test_retry_only_for_failed(client: TestClient) -> None:
    response = client.post("/api/admin/deployments/yonder-chat-mock-primary/retry")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.INVALID_REQUEST.value


@pytest.mark.asyncio
async def test_rehydrate_reattaches_ready_mock_provider(client: TestClient) -> None:
    """Persisted READY must be re-bound into the provider after a cold start."""
    deployment_id = "yonder-chat-mock-primary"
    provider = client.app.state.providers.require("mock")
    # Bootstrap marks mock READY without calling provider.load (mock chat does
    # not consult _loaded). Rehydrate is what attaches the provider mapping.
    provider._loaded.clear()
    outcomes = await client.app.state.lifecycle.rehydrate_ready_deployments()
    assert outcomes.get(deployment_id) == "rehydrated"
    assert deployment_id in provider._loaded
    assert client.app.state.routes.deployments[deployment_id].status.value == "ready"

    # Second pass simulates another restart with an already-attached provider.
    provider._loaded.clear()
    outcomes = await client.app.state.lifecycle.rehydrate_ready_deployments()
    assert outcomes.get(deployment_id) == "rehydrated"
    assert deployment_id in provider._loaded


@pytest.mark.asyncio
async def test_rehydrate_marks_unavailable_when_provider_load_fails(
    client: TestClient,
) -> None:
    from app.providers.mock import MockStreamingProvider

    class ExplodingProvider(MockStreamingProvider):
        async def load(self, deployment):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated rehydrate failure")

    deployment_id = "yonder-chat-mock-primary"
    client.app.state.providers.register("mock", ExplodingProvider())
    outcomes = await client.app.state.lifecycle.rehydrate_ready_deployments()
    assert outcomes.get(deployment_id) == "failed"
    restored = client.app.state.routes.deployments[deployment_id]
    assert restored.status.value == "unavailable"
    assert "simulated rehydrate failure" in (restored.failure_reason or "")
    assert client.app.state.routes.aliases["yonder-chat"].state.value == "unavailable"


def test_failed_unload_is_persisted(client: TestClient) -> None:
    from app.main import create_app
    from app.providers.mock import MockStreamingProvider

    class FailingUnloadProvider(MockStreamingProvider):
        async def unload(self, deployment_id: str) -> None:
            raise RuntimeError("simulated unload failure")

    deployment_id = "yonder-chat-mock-primary"
    client.app.state.providers.register("mock", FailingUnloadProvider())
    response = client.post(f"/api/admin/deployments/{deployment_id}/unload")
    assert response.status_code == 502
    assert client.app.state.routes.deployments[deployment_id].status.value == "failed"

    restarted = TestClient(create_app())
    restored = restarted.app.state.routes.deployments[deployment_id]
    assert restored.status.value == "failed"
    assert restored.failure_reason == "simulated unload failure"
