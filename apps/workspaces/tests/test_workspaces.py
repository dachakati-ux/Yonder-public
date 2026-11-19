"""Tests for optional Yonder Workspaces metadata, quotas, audit, and isolation (M05.1/M05.2)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from workspaces.api import create_app

TOKEN = "workspaces-service-token-" + "x" * 16


def _headers(
    subject: str,
    *,
    roles: str = "student",
    tenant_id: str = "school-1",
    class_ids: str = "",
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Yonder-Subject": subject,
        "X-Yonder-Roles": roles,
        "X-Yonder-Tenant-Id": tenant_id,
    }
    if class_ids:
        headers["X-Yonder-Class-Ids"] = class_ids
    return headers


def test_workspaces_requires_service_token_and_subject(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "ws.db", service_token=TOKEN)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/workspaces/v1/capabilities").status_code == 401
    assert (
        client.get(
            "/workspaces/v1/capabilities",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).status_code
        == 401
    )
    caps = client.get("/workspaces/v1/capabilities", headers=_headers("student-a"))
    assert caps.status_code == 200
    assert caps.json()["execution_enabled"] is False
    assert caps.json()["vm_isolation"] == "not_implemented"


def test_student_isolation_teacher_class_scope_and_admin_privacy(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "ws.db", service_token=TOKEN)
    client = TestClient(app)

    created = client.post(
        "/workspaces/v1/projects",
        headers=_headers("student-a", class_ids="cs-101"),
        json={"name": "Lab Essay", "class_id": "cs-101", "quota_mb": 256},
    )
    assert created.status_code == 201
    pid = created.json()["project"]["project_id"]

    # Student B cannot read, export, or delete Student A's project.
    assert (
        client.get(f"/workspaces/v1/projects/{pid}", headers=_headers("student-b")).status_code
        == 403
    )
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}/export", headers=_headers("student-b")
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/workspaces/v1/projects/{pid}", headers=_headers("student-b")).status_code
        == 403
    )

    # Assigned teacher in same tenant can read/export, but NOT delete; other-class teacher denied.
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}",
            headers=_headers("teacher-1", roles="teacher", class_ids="cs-101"),
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/workspaces/v1/projects/{pid}",
            headers=_headers("teacher-1", roles="teacher", class_ids="cs-101"),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}",
            headers=_headers("teacher-2", roles="teacher", class_ids="cs-999"),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}",
            headers=_headers(
                "teacher-other", roles="teacher", tenant_id="school-2", class_ids="cs-101"
            ),
        ).status_code
        == 403
    )

    # IT admin can read tenant audit & policies, NOT student project contents automatically.
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}",
            headers=_headers("admin-1", roles="it_admin"),
        ).status_code
        == 403
    )
    audit = client.get(
        "/workspaces/v1/admin/audit",
        headers=_headers("admin-1", roles="it_admin"),
    )
    assert audit.status_code == 200
    assert any(ev["action"] == "access_denied" for ev in audit.json()["events"])

    # Owner can export and delete their own project.
    assert (
        client.get(
            f"/workspaces/v1/projects/{pid}/export", headers=_headers("student-a")
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/workspaces/v1/projects/{pid}", headers=_headers("student-a")
        ).status_code
        == 200
    )


def test_workspace_quotas_enforced(tmp_path) -> None:
    app = create_app(
        database_path=tmp_path / "ws.db",
        service_token=TOKEN,
        max_projects_per_user=1,
        max_quota_mb_per_project=512,
    )
    client = TestClient(app)
    too_big = client.post(
        "/workspaces/v1/projects",
        headers=_headers("student-a"),
        json={"name": "Too Big", "quota_mb": 1024},
    )
    assert too_big.status_code == 409

    ok = client.post(
        "/workspaces/v1/projects",
        headers=_headers("student-a"),
        json={"name": "Project 1", "quota_mb": 256},
    )
    assert ok.status_code == 201
    second = client.post(
        "/workspaces/v1/projects",
        headers=_headers("student-a"),
        json={"name": "Project 2", "quota_mb": 256},
    )
    assert second.status_code == 409
