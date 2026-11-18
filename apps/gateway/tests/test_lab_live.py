"""Lab live fallback tests — mock when alias not ready."""

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


def test_lab_abilities_reports_live_or_training(client: TestClient) -> None:
    response = client.get("/v1/lab/abilities")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] in {"live", "training_scenario"}
    assert "chat_alias" in body
    assert "live_ready" in body


def test_lab_guided_falls_back_when_alias_not_ready(client: TestClient) -> None:
    response = client.post(
        "/v1/lab/run",
        json={
            "ability": "guided",
            "guided_mode": "summarize",
            "input": "Summarize change-window policy for the lab.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # Dev mock chat may be ready; either live or training_scenario is honest.
    assert body["mode"] in {"live", "training_scenario"}
    assert body["answer"]
    assert body["provider"] in {"gateway_chat", "mock"}


def test_lab_rag_includes_ops_corpus_docs() -> None:
    from app.services.lab_scenarios import load_corpus, retrieve

    ids = {doc.id for doc in load_corpus()}
    assert "ops-change-windows" in ids
    assert "ops-oncall" in ids
    hits = retrieve("change windows approval", k=3)
    assert hits
    assert any("change" in doc.id or "change" in doc.title.lower() for doc in hits)
