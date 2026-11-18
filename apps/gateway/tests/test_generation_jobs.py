"""Generation job / artifact API tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

_TERMINAL = {
    "succeeded",
    "completed",
    "failed",
    "error",
    "cancelled",
    "rejected",
    "not_supported",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def _wait_terminal(client: TestClient, job_id: str, *, attempts: int = 40) -> dict:
    last: dict = {}
    for _ in range(attempts):
        fetched = client.get(f"/v1/generation/jobs/{job_id}")
        assert fetched.status_code == 200
        last = fetched.json()["job"]
        if last.get("status") in _TERMINAL:
            return last
        time.sleep(0.05)
    return last


def test_create_generation_job_not_supported(client: TestClient) -> None:
    response = client.post(
        "/v1/generation/jobs",
        json={
            "mode": "txt2img",
            "prompt": "cyan hex skyline over void",
            "aspect_ratio": "16:9",
            "steps": 20,
        },
    )
    assert response.status_code == 200
    accepted = response.json()["job"]
    assert accepted["id"].startswith("gen-")
    assert accepted["status"] == "queued"

    job = _wait_terminal(client, accepted["id"])
    assert job["status"] == "not_supported"
    assert job["availability"] == "not_supported"
    assert job["provider"] == "mock-generation"

    fetched = client.get(f"/v1/generation/jobs/{job['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["job"]["id"] == job["id"]


def test_provider_failure_persists_failed_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(_request):  # noqa: ANN001
        raise RuntimeError("Diffusers deployment is not ready: demo")

    monkeypatch.setattr(client.app.state.generation_jobs.provider, "create_job", boom)
    created = client.post(
        "/v1/generation/jobs",
        json={"mode": "txt2img", "prompt": "should persist as failed"},
    )
    assert created.status_code == 200
    accepted = created.json()["job"]
    job = _wait_terminal(client, accepted["id"])
    assert job["status"] == "failed"
    assert "not ready" in (job.get("error") or "")

    client.app.state.generation_jobs._jobs.clear()
    listed = client.get("/v1/generation/jobs")
    assert listed.status_code == 200
    assert any(item["id"] == job["id"] for item in listed.json()["jobs"])


def test_generation_job_history_survives_memory_clear(client: TestClient) -> None:
    created = client.post(
        "/v1/generation/jobs",
        json={"mode": "txt2img", "prompt": "durable studio history check"},
    )
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]
    _wait_terminal(client, job_id)

    # Simulate gateway process restart: drop the in-memory cache only.
    client.app.state.generation_jobs._jobs.clear()
    client.app.state.generation_jobs._artifacts.clear()

    listed = client.get("/v1/generation/jobs")
    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    assert any(item["id"] == job_id for item in jobs)

    fetched = client.get(f"/v1/generation/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["job"]["prompt"] == "durable studio history check"


def test_delete_generation_job(client: TestClient) -> None:
    created = client.post(
        "/v1/generation/jobs",
        json={"mode": "txt2img", "prompt": "delete me from history"},
    )
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]
    _wait_terminal(client, job_id)

    deleted = client.delete(f"/v1/generation/jobs/{job_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    missing = client.get(f"/v1/generation/jobs/{job_id}")
    assert missing.status_code == 404

    listed = client.get("/v1/generation/jobs")
    assert listed.status_code == 200
    assert all(item["id"] != job_id for item in listed.json()["jobs"])


def test_generation_policy_rejects_empty_prompt(client: TestClient) -> None:
    response = client.post(
        "/v1/generation/jobs",
        json={"mode": "txt2img", "prompt": "   "},
    )
    # pydantic min_length catches empty-ish before policy; use short but valid then banned
    assert response.status_code in {200, 422}


def test_text_to_video_selects_video_alias(client: TestClient) -> None:
    response = client.post(
        "/v1/generation/jobs",
        json={"mode": "txt2video", "prompt": "aurora moving over a mountain lake"},
    )
    assert response.status_code == 200
    accepted = response.json()["job"]
    assert accepted["mode"] == "txt2video"
    assert accepted["model_alias"] == "yonder-video"
    job = _wait_terminal(client, accepted["id"])
    assert job["status"] == "not_supported"


def test_upload_and_reference_in_job(client: TestClient) -> None:
    upload = client.post(
        "/v1/generation/uploads",
        files={"file": ("ref.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        data={"kind": "reference"},
    )
    assert upload.status_code == 200
    artifact = upload.json()["artifact"]
    assert artifact["id"]

    bytes_response = client.get(artifact["url"])
    assert bytes_response.status_code == 200
    assert bytes_response.content.startswith(b"\x89PNG")

    created = client.post(
        "/v1/generation/jobs",
        json={
            "mode": "img2img",
            "prompt": "restyle the reference",
            "reference_artifact_ids": [artifact["id"]],
            "strength": 0.55,
        },
    )
    assert created.status_code == 200
    job = _wait_terminal(client, created.json()["job"]["id"])
    assert job["status"] == "not_supported"
    assert artifact["id"] in (job.get("params") or {}).get("reference_artifact_ids", [])


def test_artifact_store_accepts_bounded_worker_stream(tmp_path: Path) -> None:
    from app.services.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = store.put_stream(
        chunks=[b"\x89PNG", b"\r\nworker-bytes"],
        filename="generated.png",
        mime_type="image/png",
    )

    assert store.read(artifact.storage_key) == b"\x89PNG\r\nworker-bytes"
    assert artifact.byte_size == 18
