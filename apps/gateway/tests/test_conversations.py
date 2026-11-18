"""Gateway conversation API tests."""

from __future__ import annotations

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


def test_conversation_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/chat/conversations",
        json={"title": "First chat", "model_alias": "yonder-chat"},
    )
    assert created.status_code == 200
    convo = created.json()["conversation"]
    convo_id = convo["id"]
    assert convo["title"] == "First chat"

    listed = client.get("/api/chat/conversations")
    assert listed.status_code == 200
    assert any(item["id"] == convo_id for item in listed.json()["conversations"])

    msg = client.post(
        f"/api/chat/conversations/{convo_id}/messages",
        json={"role": "user", "content": "hello"},
    )
    assert msg.status_code == 200

    assistant = client.post(
        f"/api/chat/conversations/{convo_id}/messages",
        json={"role": "assistant", "content": "hi there", "request_id": "req_test"},
    )
    assert assistant.status_code == 200

    gen = client.post(
        f"/api/chat/conversations/{convo_id}/messages",
        json={
            "role": "assistant",
            "content": "Generated image.",
            "metadata": {
                "kind": "generation",
                "job_id": "diffusers-req_test",
                "artifact_id": "art_test",
                "mime_type": "image/png",
                "mode": "txt2img",
                "status": "succeeded",
            },
        },
    )
    assert gen.status_code == 200
    assert gen.json()["message"]["metadata"]["kind"] == "generation"

    replaced = client.post(
        f"/api/chat/conversations/{convo_id}/messages/replace-assistant",
        json={"content": "hi again", "request_id": "req_regen"},
    )
    assert replaced.status_code == 200
    assert replaced.json()["message"]["content"] == "hi again"

    renamed = client.patch(
        f"/api/chat/conversations/{convo_id}",
        json={"title": "Renamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["conversation"]["title"] == "Renamed"

    fetched = client.get(f"/api/chat/conversations/{convo_id}")
    assert fetched.status_code == 200
    messages = fetched.json()["conversation"]["messages"]
    assert len(messages) == 3
    assert messages[2]["metadata"]["artifact_id"] == "art_test"

    deleted = client.delete(f"/api/chat/conversations/{convo_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    missing = client.get(f"/api/chat/conversations/{convo_id}")
    assert missing.status_code == 404
