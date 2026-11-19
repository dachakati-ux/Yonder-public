"""Tests for institutional Academy & Small-Business workflows (M06.1-M06.5)."""

from __future__ import annotations

from types import MappingProxyType

from app.api import academy_org as academy_org_api
from app.context import RequestContextMiddleware
from app.services.academy_org import AcademyOrgStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yonder_contracts.principal import RequestPrincipal


class _HeaderPrincipalProvider:
    async def resolve(self, request) -> RequestPrincipal:
        subject = request.headers.get("X-Test-Subject", "admin-1")
        roles = tuple(r for r in request.headers.get("X-Test-Roles", "").split(",") if r)
        caps = tuple(c for c in request.headers.get("X-Test-Caps", "").split(",") if c)
        tenant = request.headers.get("X-Test-Tenant", "school-1")
        return RequestPrincipal(
            subject=subject,
            principal_type="user",
            roles=roles,
            capabilities=caps,
            attributes=MappingProxyType({"tenant_id": tenant}),
        )


def _make_app(tmp_path) -> FastAPI:
    app = FastAPI()
    app.state.academy_org_store = AcademyOrgStore(tmp_path / "academy-org.db")
    app.add_middleware(
        RequestContextMiddleware,
        provider=_HeaderPrincipalProvider(),
        service_name="test",
    )
    app.include_router(academy_org_api.router)
    return app


def _hdr(subject: str, *, roles: str, caps: str, tenant: str = "school-1") -> dict[str, str]:
    return {
        "X-Test-Subject": subject,
        "X-Test-Roles": roles,
        "X-Test-Caps": caps,
        "X-Test-Tenant": tenant,
    }


def test_academy_org_classes_enrollment_assignments_and_isolation(tmp_path) -> None:
    client = TestClient(_make_app(tmp_path))
    admin_h = _hdr("admin-1", roles="it_admin", caps="operations.manage,operations.view")
    t1_h = _hdr("teacher-1", roles="teacher", caps="academy.instruct,lab.use")
    t2_h = _hdr("teacher-2", roles="teacher", caps="academy.instruct,lab.use")
    s1_h = _hdr("student-1", roles="student", caps="lab.use,chat.use")
    s2_h = _hdr("student-2", roles="student", caps="lab.use,chat.use")

    # Configure K-12 org settings (disallows student agents, caps daily tokens at 1000).
    tpls = client.get("/v1/academy/org/templates", headers=admin_h)
    assert tpls.status_code == 200
    assert "small_business" in tpls.json()["templates"]

    cfg = client.put(
        "/v1/academy/org/settings",
        headers=admin_h,
        json={
            "org_name": "Oakridge High",
            "profile_template": "k12_school",
            "allowed_model_aliases": ["yonder-chat"],
            "max_tokens_per_day_per_user": 1000,
            "allow_student_agents": False,
        },
    )
    assert cfg.status_code == 200
    assert cfg.json()["allow_student_agents"] is False

    # Teacher-1 creates a class and enrolls student-1 (not student-2).
    cls_res = client.post(
        "/v1/academy/org/classes",
        headers=t1_h,
        json={"title": "Intro AI 101", "model_guidance": "Use yonder-chat."},
    )
    assert cls_res.status_code == 201
    class_id = cls_res.json()["class"]["class_id"]

    assert (
        client.post(
            f"/v1/academy/org/classes/{class_id}/enroll",
            headers=t1_h,
            json={"student_subject": "student-1", "role": "student"},
        ).status_code
        == 200
    )

    asg_res = client.post(
        f"/v1/academy/org/classes/{class_id}/assignments",
        headers=t1_h,
        json={"title": "Lab 1", "instructions": "Summarize prompt safety."},
    )
    assert asg_res.status_code == 201
    asg_id = asg_res.json()["assignment"]["assignment_id"]

    # Unenrolled student-2 cannot submit or read assignments.
    assert (
        client.get(f"/v1/academy/org/classes/{class_id}/assignments", headers=s2_h).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/academy/org/assignments/{asg_id}/submit",
            headers=s2_h,
            json={"summary": "Unauthorized", "token_usage_estimate": 100},
        ).status_code
        == 403
    )

    # Enrolled student-1 submits and sees only own submission.
    sub_res = client.post(
        f"/v1/academy/org/assignments/{asg_id}/submit",
        headers=s1_h,
        json={"summary": "My lab work", "token_usage_estimate": 600},
    )
    assert sub_res.status_code == 201
    s1_subs = client.get(
        f"/v1/academy/org/assignments/{asg_id}/submissions", headers=s1_h
    )
    assert len(s1_subs.json()["submissions"]) == 1

    # Teacher-2 (different class) cannot read Teacher-1's class submissions.
    assert (
        client.get(f"/v1/academy/org/assignments/{asg_id}/submissions", headers=t2_h).status_code
        == 403
    )
    # Teacher-1 sees student-1's submission.
    t1_subs = client.get(
        f"/v1/academy/org/assignments/{asg_id}/submissions", headers=t1_h
    )
    assert len(t1_subs.json()["submissions"]) == 1

    # Policy checks: disallowed agent, disallowed model, and daily token quota (600 + 500 > 1000).
    assert (
        client.post(
            "/v1/academy/org/policy-check",
            headers=s1_h,
            json={
                "class_id": class_id,
                "model_alias": "yonder-chat",
                "requested_tokens": 100,
                "uses_agent": True,
            },
        ).json()["allowed"]
        is False
    )
    assert (
        client.post(
            "/v1/academy/org/policy-check",
            headers=s1_h,
            json={"class_id": class_id, "model_alias": "yonder-coder", "requested_tokens": 100},
        ).json()["allowed"]
        is False
    )
    assert (
        client.post(
            "/v1/academy/org/policy-check",
            headers=s1_h,
            json={"class_id": class_id, "model_alias": "yonder-chat", "requested_tokens": 500},
        ).json()["allowed"]
        is False
    )
    assert (
        client.post(
            "/v1/academy/org/policy-check",
            headers=s1_h,
            json={"class_id": class_id, "model_alias": "yonder-chat", "requested_tokens": 200},
        ).json()["allowed"]
        is True
    )

    # Privacy export, deletion, and admin diagnostics.
    assert client.get("/v1/academy/org/privacy/export/student-1", headers=s2_h).status_code == 403
    exp = client.get("/v1/academy/org/privacy/export/student-1", headers=s1_h)
    assert exp.status_code == 200
    assert len(exp.json()["submissions"]) == 1

    deleted = client.delete("/v1/academy/org/privacy/student-1", headers=admin_h)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_submissions"] == 1

    diag = client.get("/v1/academy/org/admin/diagnostics", headers=admin_h)
    assert diag.status_code == 200
    assert any(ev["action"] == "student_data_deleted" for ev in diag.json()["recent_audit"])
