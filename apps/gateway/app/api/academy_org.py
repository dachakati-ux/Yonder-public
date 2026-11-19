"""Institutional Academy & Small-Business API routes (M06.1-M06.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.context import get_principal
from app.services.academy_org import ORG_TEMPLATES, AcademyOrgError, AcademyOrgStore

router = APIRouter(prefix="/v1/academy/org", tags=["academy-org"])


def _store(request: Request) -> AcademyOrgStore:
    existing = getattr(request.app.state, "academy_org_store", None)
    if existing is not None:
        return existing
    state_file = Path(request.app.state.settings.state_file)
    store = AcademyOrgStore(state_file.parent / "academy-org.db")
    request.app.state.academy_org_store = store
    return store


def _tenant(principal) -> str:
    return (principal.attributes.get("tenant_id") or "").strip() or "default"


class OrgSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_name: str = Field(min_length=1, max_length=120)
    profile_template: Literal["k12_school", "university", "small_business"] = "k12_school"
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    privacy_notice: str | None = Field(default=None, max_length=1000)
    allowed_model_aliases: list[str] = Field(default_factory=list, max_length=50)
    max_tokens_per_day_per_user: int | None = Field(default=None, ge=100, le=10_000_000)
    allow_student_workspaces: bool | None = None
    allow_student_agents: bool | None = None


class CreateClassBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=120)
    model_guidance: str = Field(default="", max_length=1000)
    allowed_model_aliases: list[str] = Field(default_factory=list, max_length=50)
    max_workspace_quota_mb: int = Field(default=512, ge=64, le=10240)
    retention_days: int = Field(default=180, ge=1, le=3650)


class EnrollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_subject: str = Field(min_length=1, max_length=128)
    role: Literal["student", "ta"] = "student"


class CreateAssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=160)
    instructions: str = Field(min_length=1, max_length=4000)
    model_guidance: str = Field(default="", max_length=1000)
    due_at: str | None = Field(default=None, max_length=64)


class SubmitAssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    token_usage_estimate: int = Field(default=250, ge=0, le=500_000)


class PolicyCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: str | None = Field(default=None, max_length=64)
    model_alias: str = Field(min_length=1, max_length=128)
    requested_tokens: int = Field(default=500, ge=1, le=500_000)
    uses_agent: bool = False


@router.get("/templates")
async def list_org_templates() -> dict[str, Any]:
    return {"templates": ORG_TEMPLATES}


@router.get("/settings")
async def get_org_settings(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    return _store(request).get_org_settings(_tenant(principal))


@router.put("/settings")
async def update_org_settings(request: Request, body: OrgSettingsBody) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("operations.manage"):
        raise HTTPException(status_code=403, detail="operations.manage required.")
    try:
        return _store(request).upsert_org_settings(
            tenant_id=_tenant(principal),
            org_name=body.org_name,
            profile_template=body.profile_template,
            retention_days=body.retention_days,
            privacy_notice=body.privacy_notice,
            allowed_model_aliases=body.allowed_model_aliases,
            max_tokens_per_day_per_user=body.max_tokens_per_day_per_user,
            allow_student_workspaces=body.allow_student_workspaces,
            allow_student_agents=body.allow_student_agents,
            actor_subject=principal.subject,
        )
    except AcademyOrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/classes")
async def list_classes(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    is_admin = principal.has_capability("operations.manage")
    classes = _store(request).list_classes_for_user(
        tenant_id=_tenant(principal),
        subject=principal.subject,
        is_admin=is_admin,
    )
    return {"classes": classes}


@router.post("/classes", status_code=201)
async def create_class(request: Request, body: CreateClassBody) -> dict[str, Any]:
    principal = get_principal(request)
    if not (
        principal.has_capability("academy.instruct")
        or principal.has_capability("operations.manage")
    ):
        raise HTTPException(
            status_code=403, detail="academy.instruct or operations.manage required."
        )
    cls = _store(request).create_class(
        tenant_id=_tenant(principal),
        title=body.title,
        instructor_subject=principal.subject,
        model_guidance=body.model_guidance,
        allowed_model_aliases=body.allowed_model_aliases,
        max_workspace_quota_mb=body.max_workspace_quota_mb,
        retention_days=body.retention_days,
        actor_subject=principal.subject,
    )
    return {"class": cls}


@router.post("/classes/{class_id}/enroll")
async def enroll_student(
    class_id: str, request: Request, body: EnrollBody
) -> dict[str, Any]:
    principal = get_principal(request)
    store = _store(request)
    cls = store.get_class(class_id)
    if cls is None or cls["tenant_id"] != _tenant(principal):
        raise HTTPException(status_code=404, detail="Class not found.")
    if cls["instructor_subject"] != principal.subject and not principal.has_capability(
        "operations.manage"
    ):
        raise HTTPException(status_code=403, detail="Only class instructor or admin can enroll.")
    enrollment = store.enroll_student(
        class_id=class_id,
        tenant_id=cls["tenant_id"],
        student_subject=body.student_subject,
        role=body.role,
        actor_subject=principal.subject,
    )
    return {"enrollment": enrollment}


@router.get("/classes/{class_id}/assignments")
async def list_assignments(class_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    store = _store(request)
    cls = store.get_class(class_id)
    if cls is None or cls["tenant_id"] != _tenant(principal):
        raise HTTPException(status_code=404, detail="Class not found.")
    is_member = (
        cls["instructor_subject"] == principal.subject
        or store.is_enrolled(class_id, principal.subject)
        or principal.has_capability("operations.manage")
    )
    if not is_member:
        raise HTTPException(status_code=403, detail="Not enrolled in this class.")
    return {"assignments": store.list_assignments(class_id)}


@router.post("/classes/{class_id}/assignments", status_code=201)
async def create_assignment(
    class_id: str, request: Request, body: CreateAssignmentBody
) -> dict[str, Any]:
    principal = get_principal(request)
    store = _store(request)
    cls = store.get_class(class_id)
    if cls is None or cls["tenant_id"] != _tenant(principal):
        raise HTTPException(status_code=404, detail="Class not found.")
    if cls["instructor_subject"] != principal.subject and not principal.has_capability(
        "operations.manage"
    ):
        raise HTTPException(status_code=403, detail="Only class instructor or admin can assign.")
    asg = store.create_assignment(
        class_id=class_id,
        tenant_id=cls["tenant_id"],
        title=body.title,
        instructions=body.instructions,
        model_guidance=body.model_guidance,
        due_at=body.due_at,
        actor_subject=principal.subject,
    )
    return {"assignment": asg}


@router.post("/assignments/{assignment_id}/submit", status_code=201)
async def submit_assignment(
    assignment_id: str, request: Request, body: SubmitAssignmentBody
) -> dict[str, Any]:
    principal = get_principal(request)
    store = _store(request)
    asg = store.get_assignment(assignment_id)
    if asg is None or asg["tenant_id"] != _tenant(principal):
        raise HTTPException(status_code=404, detail="Assignment not found.")
    if not store.is_enrolled(asg["class_id"], principal.subject):
        raise HTTPException(status_code=403, detail="Must be enrolled in class to submit.")
    sub = store.submit_assignment(
        assignment_id=assignment_id,
        class_id=asg["class_id"],
        tenant_id=asg["tenant_id"],
        student_subject=principal.subject,
        summary=body.summary,
        token_usage_estimate=body.token_usage_estimate,
    )
    return {"submission": sub}


@router.get("/assignments/{assignment_id}/submissions")
async def list_submissions(assignment_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    store = _store(request)
    asg = store.get_assignment(assignment_id)
    if asg is None or asg["tenant_id"] != _tenant(principal):
        raise HTTPException(status_code=404, detail="Assignment not found.")
    cls = store.get_class(asg["class_id"])
    if cls is None:
        raise HTTPException(status_code=404, detail="Class not found.")
    if cls["instructor_subject"] == principal.subject:
        return {"submissions": store.list_submissions(assignment_id=assignment_id)}
    if store.is_enrolled(cls["class_id"], principal.subject):
        # Students only ever see their own submissions.
        return {
            "submissions": store.list_submissions(
                assignment_id=assignment_id, student_subject=principal.subject
            )
        }
    raise HTTPException(status_code=403, detail="Cross-classroom submission access denied.")


@router.post("/policy-check")
async def policy_check(request: Request, body: PolicyCheckBody) -> dict[str, Any]:
    principal = get_principal(request)
    return _store(request).check_usage_policy(
        tenant_id=_tenant(principal),
        class_id=body.class_id,
        subject=principal.subject,
        model_alias=body.model_alias,
        requested_tokens=body.requested_tokens,
        uses_agent=body.uses_agent,
    )


@router.get("/privacy/export/{student_subject}")
async def export_student_data(student_subject: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if principal.subject != student_subject and not principal.has_capability("operations.manage"):
        raise HTTPException(status_code=403, detail="Access denied.")
    return _store(request).export_student_data(
        tenant_id=_tenant(principal),
        student_subject=student_subject,
        actor_subject=principal.subject,
    )


@router.delete("/privacy/{student_subject}")
async def delete_student_data(student_subject: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if principal.subject != student_subject and not principal.has_capability("operations.manage"):
        raise HTTPException(status_code=403, detail="Access denied.")
    return _store(request).delete_student_data(
        tenant_id=_tenant(principal),
        student_subject=student_subject,
        actor_subject=principal.subject,
    )


@router.post("/privacy/purge-expired")
async def purge_expired(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("operations.manage"):
        raise HTTPException(status_code=403, detail="operations.manage required.")
    purged = _store(request).purge_expired_submissions(
        tenant_id=_tenant(principal), actor_subject=principal.subject
    )
    return {"purged_submissions": purged}


@router.get("/admin/diagnostics")
async def org_diagnostics(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if not (
        principal.has_capability("operations.manage")
        or principal.has_capability("operations.view")
    ):
        raise HTTPException(
            status_code=403, detail="operations.view or operations.manage required."
        )
    return _store(request).diagnostics(_tenant(principal))
