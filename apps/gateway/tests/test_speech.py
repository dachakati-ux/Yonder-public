"""Gateway speech API — fail closed when speech is unavailable."""

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


def test_speech_disabled_fail_closed(client: TestClient) -> None:
    client.app.state.speech.enabled = False
    status = client.get("/v1/speech/status")
    assert status.status_code == 200
    body = status.json()
    assert body["availability"] == "not_supported"

    response = client.post(
        "/v1/speech/transcribe",
        files={"file": ("clip.webm", b"not-real-audio", "audio/webm")},
    )
    assert response.status_code == 501
    err = response.json()["error"]
    assert err["details"]["availability"] == "not_supported"

    synth = client.post("/v1/speech/synthesize", json={"text": "hello"})
    assert synth.status_code == 501
    assert synth.json()["error"]["details"]["availability"] == "not_supported"


def test_synthesize_rejects_empty_text(client: TestClient) -> None:
    response = client.post("/v1/speech/synthesize", json={"text": ""})
    assert response.status_code in {400, 422}
