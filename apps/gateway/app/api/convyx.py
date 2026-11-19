"""Optional Convyx agent-collaboration endpoints (M07)."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.context import get_principal
from app.services.convyx import ConvyxAdapter, ConvyxConfig, ConvyxError

router = APIRouter(prefix="/v1/convyx", tags=["convyx"])


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=2, max_length=64)
    summary: str = Field(min_length=1, max_length=1000)
    target_agent: str = Field(min_length=2, max_length=64)
    context_scope: Literal["project", "task"] = "project"
    allowed_tools: list[str] = Field(default_factory=list, max_length=20)
    max_tokens: int = Field(default=8000, ge=100, le=200_000)
    max_steps: int = Field(default=8, ge=1, le=25)
    model_alias: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=64)
    class_id: str | None = Field(default=None, max_length=64)


class MemoryIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str | None = Field(default=None, max_length=64)
    project_id: str = Field(min_length=2, max_length=64)
    key: str = Field(min_length=2, max_length=64)
    content: str = Field(min_length=1, max_length=4000)


class MemoryQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str | None = Field(default=None, max_length=64)
    project_id: str = Field(min_length=2, max_length=64)
    query: str = Field(min_length=1, max_length=500)


ACADEMY_AGENT_LAB_SCENARIOS = [
    {
        "id": "planner-reviewer-handoff",
        "title": "Planner & Reviewer Accountability",
        "roles": ["planner", "reviewer"],
        "objective": (
            "Delegate a scoped code-review task with explicit tool allowlist and token cap."
        ),
    },
    {
        "id": "least-privilege-budget",
        "title": "Least-Privilege Tool & Budget Guardrails",
        "roles": ["coder"],
        "objective": (
            "Verify wildcard tools and autonomous host provisioning are denied fail-closed."
        ),
    },
]


def _adapter(request: Request) -> ConvyxAdapter:
    existing = getattr(request.app.state, "convyx_adapter", None)
    if existing is not None:
        return existing
    return ConvyxAdapter(ConvyxConfig.from_env())


@router.get("/status")
async def convyx_status(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if (
        not principal.has_capability("coder.use")
        and not principal.has_capability("operations.view")
    ):
        raise HTTPException(status_code=403, detail="coder.use or operations.view required.")
    return await _adapter(request).status()


@router.post("/handoffs")
async def create_handoff(request: Request, body: HandoffRequest) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("coder.use"):
        raise HTTPException(status_code=403, detail="coder.use capability required.")
    adapter = _adapter(request)
    if not adapter.is_configured():
        raise HTTPException(status_code=503, detail="Convyx adapter is not configured.")
    try:
        return await adapter.handoff_with_delegation(
            principal,
            task_id=body.task_id,
            summary=body.summary,
            target_agent=body.target_agent,
            context_scope=body.context_scope,
            allowed_tools=body.allowed_tools,
            max_tokens=body.max_tokens,
            max_steps=body.max_steps,
            model_alias=body.model_alias,
            project_id=body.project_id,
            class_id=body.class_id,
        )
    except ConvyxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/handoffs/{handoff_id}")
async def get_handoff(request: Request, handoff_id: str) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("coder.use"):
        raise HTTPException(status_code=403, detail="coder.use capability required.")
    adapter = _adapter(request)
    if not adapter.is_configured():
        raise HTTPException(status_code=503, detail="Convyx adapter is not configured.")
    try:
        return await adapter.get_handoff(principal, handoff_id)
    except (ConvyxError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/handoffs/{handoff_id}/resume")
async def resume_handoff(request: Request, handoff_id: str) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("coder.use"):
        raise HTTPException(status_code=403, detail="coder.use capability required.")
    adapter = _adapter(request)
    if not adapter.is_configured():
        raise HTTPException(status_code=503, detail="Convyx adapter is not configured.")
    try:
        return await adapter.resume_handoff(principal, handoff_id)
    except (ConvyxError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _enforce_tenant_match(principal, requested_tenant: str | None) -> None:
    principal_tenant = (principal.attributes.get("tenant_id") or "").strip()
    if requested_tenant and principal_tenant and requested_tenant != principal_tenant:
        raise HTTPException(status_code=403, detail="Cross-tenant Convyx context access denied.")


@router.post("/memory")
async def index_memory(request: Request, body: MemoryIndexRequest) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("coder.use"):
        raise HTTPException(status_code=403, detail="coder.use capability required.")
    _enforce_tenant_match(principal, body.tenant_id)
    adapter = _adapter(request)
    if not adapter.is_configured():
        raise HTTPException(status_code=503, detail="Convyx adapter is not configured.")
    try:
        return await adapter.index_memory(
            principal, project_id=body.project_id, key=body.key, content=body.content
        )
    except ConvyxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/memory/query")
async def query_memory(request: Request, body: MemoryQueryRequest) -> dict[str, Any]:
    principal = get_principal(request)
    if not principal.has_capability("coder.use"):
        raise HTTPException(status_code=403, detail="coder.use capability required.")
    _enforce_tenant_match(principal, body.tenant_id)
    adapter = _adapter(request)
    if not adapter.is_configured():
        raise HTTPException(status_code=503, detail="Convyx adapter is not configured.")
    try:
        return await adapter.query_memory(
            principal, project_id=body.project_id, query=body.query
        )
    except ConvyxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/observability")
async def convyx_observability(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if (
        not principal.has_capability("coder.use")
        and not principal.has_capability("operations.view")
    ):
        raise HTTPException(status_code=403, detail="coder.use or operations.view required.")
    return await _adapter(request).observability(principal)


@router.get("/academy-lab")
async def convyx_academy_lab(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    if not (
        principal.has_capability("lab.use")
        or principal.has_capability("coder.use")
    ):
        raise HTTPException(status_code=403, detail="lab.use or coder.use required.")
    return {"scenarios": ACADEMY_AGENT_LAB_SCENARIOS}
