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
        return await adapter.handoff_task(
            principal,
            task_id=body.task_id,
            summary=body.summary,
            target_agent=body.target_agent,
            context_scope=body.context_scope,
        )
    except (ConvyxError, httpx.HTTPError) as exc:
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
