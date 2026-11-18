"""Academy Tier 2 — cohorts, instructor, workbench, capacity, fixture tools."""

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
    from app.services.cohorts import get_cohort_catalog

    get_cohort_catalog.cache_clear()
    from app.main import create_app

    return TestClient(create_app())


def test_capacity_track_and_snapshot(client: TestClient) -> None:
    body = client.get("/v1/lab/curriculum").json()
    ids = {t["id"] for t in body["tracks"]}
    assert "capacity" in ids

    snap = client.get("/v1/lab/capacity")
    assert snap.status_code == 200
    data = snap.json()
    assert "aliases" in data
    assert data["note"]
    for row in data["aliases"]:
        assert row["availability"] in {"ready", "not_ready", "not_reported"}


def test_cohorts_challenge_submit_and_board(client: TestClient) -> None:
    listed = client.get("/v1/lab/cohorts")
    assert listed.status_code == 200
    cohorts = listed.json()["cohorts"]
    assert cohorts
    challenge_ids = {c["id"] for c in cohorts[0]["challenges"]}
    assert "capacity-literacy" in challenge_ids

    miss = client.post(
        "/v1/lab/cohorts/challenges/capacity-literacy/submit",
        json={"answer": "I like GPUs"},
    )
    assert miss.status_code == 200
    assert miss.json()["passed"] is False
    assert miss.json()["criteria_missed"]

    hit = client.post(
        "/v1/lab/cohorts/challenges/capacity-literacy/submit",
        json={
            "answer": "Fund CPU for Academy progress and RAG; GPU for live try-it when READY."
        },
    )
    assert hit.status_code == 200
    assert hit.json()["passed"] is True
    assert "cpu" in hit.json()["criteria_met"]
    assert "gpu" in hit.json()["criteria_met"]
    assert "ready" in hit.json()["criteria_met"]

    board = client.get("/v1/lab/cohorts/challenges/capacity-literacy/board")
    assert board.status_code == 200
    assert board.json()["completions"]
    note = board.json()["note"].lower()
    assert "criteria" in note
    assert "rank" in note or "invented" in note


def test_support_challenge_forbids_vip(client: TestClient) -> None:
    bad = client.post(
        "/v1/lab/cohorts/challenges/support-triage-honest/submit",
        json={"answer": "triage unknown impact for VIP customer"},
    )
    assert bad.status_code == 200
    assert bad.json()["passed"] is False

    good = client.post(
        "/v1/lab/cohorts/challenges/support-triage-honest/submit",
        json={"answer": "triage with not_reported impact; escalate carefully"},
    )
    assert good.status_code == 200
    assert good.json()["passed"] is True


def test_instructor_summary_reset_and_quarantine(client: TestClient) -> None:
    client.post(
        "/v1/lab/progress/foundations-gateway-only/events",
        json={"event": "check", "selected_option_id": "b"},
    )
    summary = client.get("/v1/lab/instructor/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["principals_seen"] >= 1
    assert body["tracks"]
    assert body["class_runner"]["recommended_mission_id"] == "siteops-incident-loop"
    assert "lessons" in body["tracks"][0]
    assert body["stall_days"] == 7

    subject = client.get("/v1/lab/curriculum").json()["principal_subject"]
    reset = client.post(
        "/v1/lab/instructor/progress/reset",
        json={"principal_subject": subject},
    )
    assert reset.status_code == 200
    assert reset.json()["cleared"] >= 1

    q = client.post(
        "/v1/lab/instructor/corpus",
        json={"corpus_id": "support-fixture", "action": "quarantine"},
    )
    assert q.status_code == 200
    assert "support-fixture" in q.json()["quarantined"]

    blocked = client.post(
        "/v1/lab/sandbox",
        json={"label": "blocked", "preset": "support-fixture"},
    )
    assert blocked.status_code == 400

    release = client.post(
        "/v1/lab/instructor/corpus",
        json={"corpus_id": "support-fixture", "action": "release"},
    )
    assert release.status_code == 200
    assert "support-fixture" not in release.json()["quarantined"]


def test_workbench_notes_and_export(client: TestClient) -> None:
    created = client.post(
        "/v1/lab/workbench/notes",
        json={
            "title": "FIX-1001",
            "body": "Fixture ticket VPN flap",
            "tags": ["ticket"],
            "source": "manual",
        },
    )
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]

    listed = client.get("/v1/lab/workbench")
    assert listed.status_code == 200
    assert any(n["id"] == note_id for n in listed.json()["notes"])

    exported = client.get("/v1/lab/workbench/export")
    assert exported.status_code == 200
    assert exported.json()["notes"]
    assert "principal_subject" in exported.json()

    deleted = client.delete(f"/v1/lab/workbench/notes/{note_id}")
    assert deleted.status_code == 200


def test_ticket_tool_mission_and_fixture_tools(client: TestClient) -> None:
    missions = client.get("/v1/lab/missions").json()["missions"]
    ids = {m["id"] for m in missions}
    assert "ticket-tool-loop" in ids

    from app.services.tools import build_default_tool_executor
    from yonder_contracts.tools import ToolCall

    executor = build_default_tool_executor()
    ticket = executor.execute(
        ToolCall(id="t1", name="yonder_fixture_get_ticket", arguments={"ticket_id": "FIX-1001"})
    )
    assert "FIX-1001" in ticket.content
    assert "mock" in ticket.content.lower() or "availability" in ticket.content.lower()
    runbook = executor.execute(
        ToolCall(id="t2", name="yonder_fixture_get_runbook", arguments={"topic": "vpn-flap"})
    )
    assert "vpn" in runbook.content.lower()

    client.post("/v1/lab/missions/ticket-tool-loop/events", json={"event": "start"})
    hit = client.post(
        "/v1/lab/missions/ticket-tool-loop/events",
        json={
            "event": "check",
            "answer": "Called tools for FIX-1001 and vpn-flap runbook.",
        },
    )
    assert hit.status_code == 200
    assert hit.json()["check_correct"] is True
