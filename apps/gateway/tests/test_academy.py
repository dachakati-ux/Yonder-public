"""Academy curriculum, progress, and sandbox API tests."""

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


def test_curriculum_loads_with_tracks(client: TestClient) -> None:
    response = client.get("/v1/lab/curriculum")
    assert response.status_code == 200
    body = response.json()
    track_ids = {t["id"] for t in body["tracks"]}
    assert {
        "foundations",
        "trust",
        "site_ops",
        "operator",
        "builder",
        "capacity",
    } <= track_ids
    site = next(t for t in body["tracks"] if t["id"] == "site_ops")
    assert len(site["lessons"]) >= 12
    operator = next(t for t in body["tracks"] if t["id"] == "operator")
    assert len(operator["lessons"]) >= 6
    builder = next(t for t in body["tracks"] if t["id"] == "builder")
    assert len(builder["lessons"]) >= 6
    # Quiz answers must not leak in public curriculum.
    for lesson in site["lessons"]:
        assert "correct_option_id" not in lesson["check"]
        assert lesson["progress"]["status"] == "not_started"


def test_eval_case_check(client: TestClient) -> None:
    miss = client.post(
        "/v1/lab/progress/foundations-honest-readiness/events",
        json={"event": "check", "answer": "everything is fine always"},
    )
    assert miss.status_code == 200
    assert miss.json()["check_correct"] is False

    hit = client.post(
        "/v1/lab/progress/foundations-honest-readiness/events",
        json={
            "event": "check",
            "answer": (
                "Lab uses training_scenario mocks when the alias is not READY. "
                "Missing GPU metrics show as not_reported."
            ),
        },
    )
    assert hit.status_code == 200
    assert hit.json()["check_correct"] is True
    assert hit.json()["completed"] is True


def test_trust_track_present(client: TestClient) -> None:
    body = client.get("/v1/lab/curriculum").json()
    trust = next(t for t in body["tracks"] if t["id"] == "trust")
    assert len(trust["lessons"]) == 4
    assert trust["lessons"][0]["id"] == "trust-hallucination-traps"


def test_role_tracks_present(client: TestClient) -> None:
    body = client.get("/v1/lab/curriculum").json()
    ids = {t["id"] for t in body["tracks"]}
    assert {"support", "security", "pm", "managers"} <= ids
    support = next(t for t in body["tracks"] if t["id"] == "support")
    assert any(
        lesson["id"] == "support-capstone" for lesson in support["lessons"]
    )


def test_missions_and_check(client: TestClient) -> None:
    listed = client.get("/v1/lab/missions")
    assert listed.status_code == 200
    ids = {m["id"] for m in listed.json()["missions"]}
    assert "support-ticket-loop" in ids
    assert "operator-readiness-loop" in ids
    assert "builder-structured-ops-loop" in ids

    detail = client.get("/v1/lab/missions/support-ticket-loop")
    assert detail.status_code == 200
    assert detail.json()["mission"]["steps"]

    client.post(
        "/v1/lab/missions/support-ticket-loop/events",
        json={"event": "start"},
    )
    client.post(
        "/v1/lab/missions/support-ticket-loop/events",
        json={"event": "step", "step_id": "triage"},
    )
    miss = client.post(
        "/v1/lab/missions/support-ticket-loop/events",
        json={"event": "check", "answer": "done"},
    )
    assert miss.json()["check_correct"] is False
    hit = client.post(
        "/v1/lab/missions/support-ticket-loop/events",
        json={
            "event": "check",
            "answer": "Completed triage with citation and intake.",
        },
    )
    assert hit.status_code == 200
    assert hit.json()["check_correct"] is True
    assert hit.json()["check_passed"] is True


def test_prompt_library_reviewed_only(client: TestClient) -> None:
    body = client.get("/v1/lab/prompts").json()
    assert len(body["patterns"]) >= 9
    assert all(p["status"] == "reviewed" for p in body["patterns"])
    ids = {p["id"] for p in body["patterns"]}
    assert {
        "operator-readiness-v1",
        "siteops-health-v1",
        "siteops-rma-v1",
        "builder-structured-v1",
    } <= ids


def test_department_sandbox_presets(client: TestClient) -> None:
    presets = client.get("/v1/lab/sandbox/presets").json()["presets"]
    ids = {p["id"] for p in presets}
    assert {"site-ops-fixture", "support-fixture", "ops-fixture", "eng-fixture"} <= ids
    created = client.post(
        "/v1/lab/sandbox",
        json={"label": "support", "preset": "support-fixture"},
    )
    assert created.status_code == 200
    assert len(created.json()["sandbox"]["documents"]) >= 2


def test_progress_quiz_and_complete(client: TestClient) -> None:
    start = client.post(
        "/v1/lab/progress/foundations-gateway-only/events",
        json={"event": "start"},
    )
    assert start.status_code == 200
    assert start.json()["status"] == "in_progress"

    wrong = client.post(
        "/v1/lab/progress/foundations-gateway-only/events",
        json={"event": "check", "selected_option_id": "a"},
    )
    assert wrong.status_code == 200
    assert wrong.json()["check_correct"] is False
    assert wrong.json()["completed"] is False

    right = client.post(
        "/v1/lab/progress/foundations-gateway-only/events",
        json={"event": "check", "selected_option_id": "b"},
    )
    assert right.status_code == 200
    assert right.json()["check_correct"] is True
    assert right.json()["completed"] is True
    assert right.json()["status"] == "completed"

    progress = client.get("/v1/lab/progress")
    assert progress.status_code == 200
    assert progress.json()["lessons"]["foundations-gateway-only"]["status"] == "completed"


def test_sandbox_site_ops_fixture_retrieve_and_parts(client: TestClient) -> None:
    created = client.post(
        "/v1/lab/sandbox",
        json={"label": "desk", "preset": "site-ops-fixture"},
    )
    assert created.status_code == 200
    box = created.json()["sandbox"]
    assert len(box["documents"]) >= 4
    sandbox_id = box["id"]

    retrieved = client.post(
        f"/v1/lab/sandbox/{sandbox_id}/retrieve",
        json={"query": "Uncorrectable ECC DIMM A1", "k": 3},
    )
    assert retrieved.status_code == 200
    assert retrieved.json()["hits"]
    assert "isolated" in retrieved.json()["note"].lower()

    part = client.post(
        f"/v1/lab/sandbox/{sandbox_id}/parts",
        json={
            "part_number": "DIMM-A1-TEACH",
            "quantity": 1,
            "incident_id": "inc-1",
            "note": "practice",
        },
    )
    assert part.status_code == 200
    assert part.json()["entry"]["part_number"] == "DIMM-A1-TEACH"

    reset = client.post(f"/v1/lab/sandbox/{sandbox_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["sandbox"]["parts"] == []
    assert len(reset.json()["sandbox"]["documents"]) >= 4


def test_sandbox_quota_fail_closed(client: TestClient) -> None:
    created = client.post("/v1/lab/sandbox", json={"label": "q", "preset": None})
    sandbox_id = created.json()["sandbox"]["id"]
    # Force tiny quota for this process store.
    client.app.state.sandboxes.quotas.max_documents_per_sandbox = 1
    client.app.state.sandboxes.quotas.max_chars_per_document = 100
    ok = client.post(
        f"/v1/lab/sandbox/{sandbox_id}/documents",
        json={"title": "one", "text": "hello"},
    )
    assert ok.status_code == 200
    blocked = client.post(
        f"/v1/lab/sandbox/{sandbox_id}/documents",
        json={"title": "two", "text": "world"},
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "INVALID_REQUEST"


def test_progress_reset(client: TestClient) -> None:
    client.post(
        "/v1/lab/progress/foundations-gateway-only/events",
        json={"event": "check", "selected_option_id": "b"},
    )
    reset = client.post("/v1/lab/progress/reset")
    assert reset.status_code == 200
    assert reset.json()["cleared"] >= 1
    progress = client.get("/v1/lab/progress")
    assert progress.json()["lessons"]["foundations-gateway-only"]["status"] == "not_started"


def test_siteops_schemas_registered(client: TestClient) -> None:
    response = client.get("/v1/platform/schemas")
    assert response.status_code == 200
    ids = {item["schema_id"] for item in response.json()["schemas"]}
    assert "yonder.siteops.incident_intake.v1" in ids
    assert "yonder.siteops.rma_packet.v1" in ids
    assert "yonder.siteops.parts_ledger_entry.v1" in ids


def test_curriculum_integrity_contracts(client: TestClient) -> None:
    from app.services.curriculum import get_curriculum
    from app.services.sandbox import PRESET_LABELS

    curriculum = get_curriculum()
    known_presets = set(PRESET_LABELS)
    for track in curriculum.tracks:
        for lid in track.lesson_ids:
            assert lid in curriculum.lessons, f"track {track.id} missing lesson {lid}"
            lesson = curriculum.lessons[lid]
            try_it = lesson.try_it
            if try_it is None:
                continue
            if try_it.sandbox_preset:
                assert try_it.sandbox_preset in known_presets, (
                    f"lesson {lid}: unknown sandbox_preset {try_it.sandbox_preset}"
                )
                assert try_it.kind in {"sandbox", "playground"}, (
                    f"lesson {lid}: sandbox_preset needs sandbox/playground kind"
                )
                assert try_it.label, f"lesson {lid}: sandbox try_it missing label"
                assert try_it.href, f"lesson {lid}: sandbox try_it missing href"


def test_mission_href_contract(client: TestClient) -> None:
    from app.services.missions import get_missions
    from app.services.sandbox import PRESET_LABELS

    known_presets = set(PRESET_LABELS)
    for mission in get_missions().values():
        for step in mission.steps:
            if step.kind == "sandbox" or step.sandbox_preset:
                assert step.sandbox_preset, (
                    f"mission {mission.id} step {step.id}: sandbox_preset required"
                )
                assert step.sandbox_preset in known_presets, (
                    f"mission {mission.id} step {step.id}: "
                    f"unknown sandbox_preset {step.sandbox_preset}"
                )
            if step.kind == "recipe":
                assert step.recipe_id, (
                    f"mission {mission.id} step {step.id}: recipe_id required"
                )


def test_workbench_patch_roundtrip(client: TestClient) -> None:
    created = client.post(
        "/v1/lab/workbench/notes",
        json={
            "title": "Draft",
            "body": "Original body",
            "tags": ["draft"],
            "source": "manual",
        },
    )
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]

    patched = client.patch(
        f"/v1/lab/workbench/notes/{note_id}",
        json={
            "title": "Updated title",
            "body": "Updated body",
            "tags": ["ops", "note"],
            "source": "mission",
        },
    )
    assert patched.status_code == 200
    note = patched.json()["note"]
    assert note["title"] == "Updated title"
    assert note["body"] == "Updated body"
    assert note["tags"] == ["ops", "note"]
    assert note["source"] == "mission"
    assert note["id"] == note_id

    listed = client.get("/v1/lab/workbench")
    assert listed.status_code == 200
    match = next(n for n in listed.json()["notes"] if n["id"] == note_id)
    assert match["title"] == "Updated title"

    missing = client.patch(
        "/v1/lab/workbench/notes/note_does_not_exist",
        json={"title": "Nope"},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "INVALID_REQUEST"


def test_get_progress_lesson(client: TestClient) -> None:
    lesson_id = "foundations-gateway-only"
    before = client.get(f"/v1/lab/progress/{lesson_id}")
    assert before.status_code == 200
    body = before.json()
    assert body["lesson"]["id"] == lesson_id
    assert "correct_option_id" not in body["lesson"]["check"]
    assert body["progress"]["status"] == "not_started"
    assert body["principal_subject"]

    client.post(
        f"/v1/lab/progress/{lesson_id}/events",
        json={"event": "check", "selected_option_id": "b"},
    )
    after = client.get(f"/v1/lab/progress/{lesson_id}")
    assert after.status_code == 200
    assert after.json()["progress"]["status"] == "completed"
    assert after.json()["progress"]["check_passed"] is True

    missing = client.get("/v1/lab/progress/does-not-exist-lesson")
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "INVALID_REQUEST"
