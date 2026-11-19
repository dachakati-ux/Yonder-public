"""Adversarial M05.6 tests: concurrency race, TTL expiry, sandbox bounds, and snapshots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from workspaces.api import create_app
from workspaces.provisioner import ProxmoxAdapter

TOKEN = "workspaces-service-token-" + "x" * 16
DIGEST = "yonder-python@sha256:" + "a" * 64


def _hdr(subject: str, tenant_id: str = "school-1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Yonder-Subject": subject,
        "X-Yonder-Roles": "student",
        "X-Yonder-Tenant-Id": tenant_id,
    }


def _adapter() -> ProxmoxAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agent/exec"):
            return httpx.Response(200, json={"data": {"pid": 42, "exitcode": 0}})
        return httpx.Response(200, json={"data": "UPID:pve01:123"})

    return ProxmoxAdapter(
        api_url="https://pve.school.internal:8006",
        api_token_id="yonder-ws@pve!scoped",
        api_token_secret="tok-123",
        node="pve01",
        pool="yonder-students",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_concurrent_session_creation_respects_quota(tmp_path) -> None:
    app = create_app(
        database_path=tmp_path / "ws.db",
        service_token=TOKEN,
        max_active_sessions_per_user=1,
        provisioner=_adapter(),
    )
    client = TestClient(app)
    pid = client.post(
        "/workspaces/v1/projects",
        headers=_hdr("student-a"),
        json={"name": "Race Lab", "quota_mb": 256},
    ).json()["project"]["project_id"]

    def attempt(idx: int) -> int:
        res = client.post(
            f"/workspaces/v1/projects/{pid}/sessions",
            headers=_hdr("student-a"),
            json={"idempotency_key": f"race-key-{idx:04d}", "template_digest": DIGEST},
        )
        return res.status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(attempt, range(5)))
    assert statuses.count(201) == 1
    assert statuses.count(409) == 4


def test_sandbox_bounds_snapshots_exec_and_ttl_expiry(tmp_path) -> None:
    app = create_app(
        database_path=tmp_path / "ws.db",
        service_token=TOKEN,
        provisioner=_adapter(),
    )
    client = TestClient(app)
    pid = client.post(
        "/workspaces/v1/projects",
        headers=_hdr("student-a"),
        json={"name": "Exec Lab", "quota_mb": 256},
    ).json()["project"]["project_id"]

    # Reject oversized CPU/RAM/TTL or unsafe network_profile.
    for bad in (
        {"idempotency_key": "bad-cpu-001", "template_digest": DIGEST, "cpu_cores": 16},
        {"idempotency_key": "bad-ram-001", "template_digest": DIGEST, "memory_mb": 65536},
        {"idempotency_key": "bad-net-001", "template_digest": DIGEST, "network_profile": "host"},
    ):
        assert (
            client.post(
                f"/workspaces/v1/projects/{pid}/sessions",
                headers=_hdr("student-a"),
                json=bad,
            ).status_code
            == 422
        )

    # Snapshots: owner can create/list; Student B gets 403.
    snap = client.post(
        f"/workspaces/v1/projects/{pid}/snapshots",
        headers=_hdr("student-a"),
        json={"label": "checkpoint-1", "digest_sha256": "b" * 64},
    )
    assert snap.status_code == 201
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}/snapshots", headers=_hdr("student-b")
        ).status_code
        == 403
    )

    # Session exec succeeds for owner, denied for Student B.
    sid = client.post(
        f"/workspaces/v1/projects/{pid}/sessions",
        headers=_hdr("student-a"),
        json={"idempotency_key": "exec-sess-001", "template_digest": DIGEST, "ttl_seconds": 60},
    ).json()["session"]["session_id"]

    assert (
        client.post(
            f"/workspaces/v1/sessions/{sid}/exec",
            headers=_hdr("student-b"),
            json={"command": ["pytest"]},
        ).status_code
        == 403
    )
    ok_exec = client.post(
        f"/workspaces/v1/sessions/{sid}/exec",
        headers=_hdr("student-a"),
        json={"command": ["pytest", "-q"]},
    )
    assert ok_exec.status_code == 200
    assert ok_exec.json()["result"]["exitcode"] == 0

    # Backdate created_at beyond TTL -> exec expires session and returns 409.
    import sqlite3

    expired_ts = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    with sqlite3.connect(tmp_path / "ws.db") as db:
        db.execute(
            "UPDATE workspace_sessions SET created_at=? WHERE session_id=?",
            (expired_ts, sid),
        )
    exp_res = client.post(
        f"/workspaces/v1/sessions/{sid}/exec",
        headers=_hdr("student-a"),
        json={"command": ["pytest"]},
    )
    assert exp_res.status_code == 409
    assert "expired" in exp_res.json()["detail"].lower()
