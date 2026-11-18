"""Routing lane and worker trust policy unit tests."""

from __future__ import annotations

from app.services.worker_trust import evaluate_worker_registration
from fastapi.testclient import TestClient
from yonder_configuration.models import WorkerRegistrationPolicyConfig
from yonder_contracts.principal import RequestPrincipal


def _heartbeat(worker_id: str) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "display_name": worker_id,
        "endpoint": "http://127.0.0.1:8090",
        "agent_version": "test",
        "ready": True,
    }


def test_worker_trust_allowlist_rejects_unknown() -> None:
    policy = WorkerRegistrationPolicyConfig(
        mode="allowlist",
        allowed_worker_ids=("ok-worker",),
        accept_untrusted=False,
    )
    decision = evaluate_worker_registration(policy, "other")
    assert decision["accepted"] is False
    assert decision["trust_state"] == "rejected"


def test_worker_trust_allowlist_marks_untrusted_when_accepted() -> None:
    policy = WorkerRegistrationPolicyConfig(
        mode="allowlist",
        allowed_worker_ids=("ok-worker",),
        accept_untrusted=True,
    )
    decision = evaluate_worker_registration(policy, "other")
    assert decision["accepted"] is True
    assert decision["trusted"] is False


def test_experimental_lane_requires_capability(gateway_env) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    service = client.app.state.chat_service
    principal = RequestPrincipal(
        subject="limited",
        principal_type="user",
        capabilities=("chat.use",),
    )
    try:
        service.enforce_lane_access("yonder-chat", principal)
        raised = False
    except Exception as exc:  # noqa: BLE001
        raised = True
        assert exc.code.value == "PERMISSION_CONTEXT_REQUIRED"
    assert raised

    allowed = RequestPrincipal(
        subject="ops",
        principal_type="user",
        capabilities=("chat.use", "chat.experimental"),
    )
    service.enforce_lane_access("yonder-chat", allowed)


def test_operations_summary_includes_lanes(gateway_env) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    body = client.get("/api/admin/operations/summary").json()
    route = next(item for item in body["routes"] if item["alias"] == "yonder-chat")
    assert route["lane"] == "experimental"
    assert route["experimental"] is True


def test_unknown_worker_cannot_bypass_registration_with_heartbeat(gateway_env) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/admin/workers/unknown-worker/heartbeat",
        json=_heartbeat("unknown-worker"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKER_UNAVAILABLE"
    assert "unknown-worker" not in client.app.state.workers


def test_heartbeat_rejects_mismatched_worker_id(gateway_env) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    registration = client.post(
        "/api/admin/workers/register",
        json={**_heartbeat("registered-worker"), "capabilities": []},
    )
    assert registration.status_code == 200

    response = client.post(
        "/api/admin/workers/registered-worker/heartbeat",
        json=_heartbeat("different-worker"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
