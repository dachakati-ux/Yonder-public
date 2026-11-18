"""Academy durable stores survive gateway restart when database_url is set."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from yonder_configuration import load_settings
from yonder_configuration.models import YonderSettings

ROOT = Path(__file__).resolve().parents[3]


def test_durable_academy_survives_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sqlite_url = f"sqlite:///{tmp_path / 'academy-durable.db'}"
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )

    from app.services.cohorts import get_cohort_catalog

    get_cohort_catalog.cache_clear()

    base = load_settings()
    patched = base.model_copy(update={"database_url": sqlite_url})

    def _load_patched() -> YonderSettings:
        return patched

    monkeypatch.setattr("app.main.load_settings", _load_patched)
    monkeypatch.setattr("yonder_configuration.load_settings", _load_patched)

    from app.main import create_app

    client1 = TestClient(create_app())
    assert client1.app.state.challenges.__class__.__name__ == "SqlAlchemyChallengeStore"

    hit = client1.post(
        "/v1/lab/cohorts/challenges/capacity-literacy/submit",
        json={
            "answer": "Fund CPU for Academy progress and RAG; GPU for live try-it when READY."
        },
    )
    assert hit.status_code == 200
    assert hit.json()["passed"] is True

    client1.post("/v1/lab/missions/ticket-tool-loop/events", json={"event": "start"})
    client1.post(
        "/v1/lab/missions/ticket-tool-loop/events",
        json={"event": "step", "step_id": "tools-lab"},
    )

    note = client1.post(
        "/v1/lab/workbench/notes",
        json={"title": "durable", "body": "survives restart", "tags": ["t"]},
    )
    assert note.status_code == 200
    note_id = note.json()["note"]["id"]

    q = client1.post(
        "/v1/lab/instructor/corpus",
        json={"corpus_id": "eng-fixture", "action": "quarantine"},
    )
    assert q.status_code == 200

    client2 = TestClient(create_app())
    assert client2.app.state.challenges.__class__.__name__ == "SqlAlchemyChallengeStore"

    cohorts = client2.get("/v1/lab/cohorts").json()
    challenges = {
        c["id"]: c for cohort in cohorts["cohorts"] for c in cohort["challenges"]
    }
    assert challenges["capacity-literacy"]["submission"]["passed"] is True

    mission = client2.get("/v1/lab/missions/ticket-tool-loop").json()
    assert mission["progress"]["status"] == "in_progress"
    assert "tools-lab" in mission["progress"]["completed_steps"]

    notes = client2.get("/v1/lab/workbench").json()["notes"]
    assert any(n["id"] == note_id for n in notes)

    summary = client2.get("/v1/lab/instructor/summary").json()
    assert "eng-fixture" in summary["quarantined_corpora"]
