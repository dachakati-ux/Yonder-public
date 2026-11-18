"""End-to-end hardening scenarios for Yonder v0.1 foundation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YONDER_CONFIG_FILE", str(ROOT / "config/environments/development.yaml"))
    state = tmp_path / "e2e-state.json"
    monkeypatch.setenv("YONDER_STATE_FILE", str(state))
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{tmp_path / 'e2e.db'}")
    import sys

    sys.path.insert(0, str(ROOT / "apps/gateway"))

    def _factory() -> TestClient:
        from app.main import create_app

        return TestClient(create_app())

    return _factory, state


def test_e2e_chat_disable_restart_provider_failure(app_factory) -> None:
    create_client, state = app_factory

    with create_client() as client:
        chat = client.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert chat.status_code == 200

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "stream"}],
                "stream": True,
            },
        ) as streamed:
            assert streamed.status_code == 200
            payload = "".join(streamed.iter_text())
            assert "data:" in payload

        disabled = client.post(
            "/api/admin/routes/yonder-chat/enable",
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        blocked = client.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "nope"}],
            },
        )
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "MODEL_UNAVAILABLE"
        client.post("/api/admin/routes/yonder-chat/enable", json={"enabled": True})

        from app.providers.mock import MockStreamingProvider
        from yonder_contracts.inference import ChatRequest

        class Boom(MockStreamingProvider):
            async def chat(self, request: ChatRequest):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        client.app.state.providers.register("mock", Boom(chunk_delay_seconds=0.0))
        failed = client.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "x"}],
                "stream": False,
            },
        )
        assert failed.status_code == 502
        assert failed.json()["error"]["code"] == "PROVIDER_FAILURE"

        client.app.state.providers.register("mock", MockStreamingProvider(chunk_delay_seconds=0.0))
        client.post("/api/admin/deployments/yonder-chat-mock-primary/unload")

    with create_client() as client2:
        status = client2.get("/api/admin/deployments/yonder-chat-mock-primary").json()
        assert status["deployment"]["status"] == "stopped"

        from yonder_configuration.models import YonderSettings
        from yonder_configuration.validation import ConfigurationError, validate_settings

        with pytest.raises(ConfigurationError):
            validate_settings(
                YonderSettings(environment="production", security={"mode": "development"})
            )


def test_e2e_capacity_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YONDER_CONFIG_FILE", str(ROOT / "config/environments/development.yaml"))
    monkeypatch.setenv("YONDER_STATE_FILE", str(tmp_path / "cap-state.json"))
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{tmp_path / 'cap.db'}")
    monkeypatch.setenv("YONDER_ADMISSION__MAX_ACTIVE_REQUESTS_PER_DEPLOYMENT", "1")
    monkeypatch.setenv("YONDER_ADMISSION__MAX_QUEUE_DEPTH", "0")
    import sys

    sys.path.insert(0, str(ROOT / "apps/gateway"))
    from app.main import create_app

    with TestClient(create_app()) as client:
        client.app.state.routes.deployments["yonder-chat-mock-primary"].active_requests = 1
        overloaded = client.post(
            "/v1/chat/completions",
            json={
                "model": "yonder-chat",
                "messages": [{"role": "user", "content": "busy"}],
                "stream": False,
            },
        )
        assert overloaded.status_code == 429
        assert overloaded.json()["error"]["code"] == "CAPACITY_EXCEEDED"


def test_e2e_worker_disconnect_propagates(app_factory) -> None:
    create_client, _state = app_factory
    with create_client() as client:
        client.post(
            "/api/admin/workers/register",
            json={
                "worker_id": "e2e-worker",
                "display_name": "E2E",
                "endpoint": "http://127.0.0.1:8090",
                "agent_version": "0.1.0",
                "ready": True,
            },
        )
        client.post("/api/admin/workers/e2e-worker/disconnect")
        hardware = client.get("/api/admin/hardware").json()["nodes"][0]
        assert hardware["connected"] is False
        assert "disconnected" in hardware["source"]


def test_e2e_experimental_lane_and_worker_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YONDER_CONFIG_FILE", str(ROOT / "config/environments/development.yaml"))
    monkeypatch.setenv("YONDER_STATE_FILE", str(tmp_path / "lane-state.json"))
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{tmp_path / 'lane.db'}")
    import sys

    sys.path.insert(0, str(ROOT / "apps/gateway"))
    from app.main import create_app
    from app.services.chat import ChatServiceError
    from yonder_configuration.models import WorkerRegistrationPolicyConfig
    from yonder_contracts.principal import RequestPrincipal

    with TestClient(create_app()) as client:
        client.app.state.settings.worker_registration = WorkerRegistrationPolicyConfig(
            mode="allowlist",
            allowed_worker_ids=("trusted-worker",),
            accept_untrusted=False,
        )

        principal = RequestPrincipal(
            subject="limited",
            principal_type="user",
            capabilities=("chat.use",),
        )
        with pytest.raises(ChatServiceError) as exc:
            client.app.state.chat_service.enforce_lane_access("yonder-chat", principal)
        assert exc.value.code.value == "PERMISSION_CONTEXT_REQUIRED"

        rejected = client.post(
            "/api/admin/workers/register",
            json={
                "worker_id": "stranger",
                "display_name": "Stranger",
                "endpoint": "http://127.0.0.1:8099",
                "agent_version": "0.1.0",
                "ready": True,
            },
        )
        assert rejected.status_code == 403

        accepted = client.post(
            "/api/admin/workers/register",
            json={
                "worker_id": "trusted-worker",
                "display_name": "Trusted",
                "endpoint": "http://127.0.0.1:8090",
                "agent_version": "0.1.0",
                "ready": True,
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["worker"]["trusted"] is True

        events = client.get("/api/admin/events?limit=10").json()["events"]
        assert any(item.get("event_type") == "worker.registered" for item in events)
