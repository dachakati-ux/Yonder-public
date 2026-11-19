"""Hugging Face discovery for the model library.

Read-only catalog: no untrusted repository code, weights or remote URLs are executed.
"""
from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response

router = APIRouter(prefix="/api/admin/catalog", tags=["model-catalog"])
HF_API = "https://huggingface.co/api"
REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
GGUF_SUFFIX = ".gguf"
MAX_RESULTS = 25


def _capability(request: Request, capability: str = "models.view"):
    principal = get_principal(request)
    if principal.has_capability(capability):
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        f"Principal lacks {capability} capability.",
        get_request_id(request),
    )


async def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    # Fixed upstream + controlled paths only; user input cannot set arbitrary hosts.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0),
        follow_redirects=False,
        headers={"User-Agent": "Yonder-ModelCatalog/0.1"},
    ) as client:
        response = await client.get(HF_API + path, params=params)
        response.raise_for_status()
        return response.json()


def _catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    identifier = str(item.get("id") or item.get("modelId") or "")
    tags = item.get("tags") or []
    return {
        "id": identifier,
        "url": "https://huggingface.co/" + identifier,
        "downloads": item.get("downloads") or 0,
        "likes": item.get("likes") or 0,
        "pipeline_tag": item.get("pipeline_tag"),
        "gated": bool(item.get("gated")),
        "private": bool(item.get("private")),
        "license": next(
            (
                tag.split(":", 1)[1]
                for tag in tags
                if isinstance(tag, str) and tag.startswith("license:")
            ),
            None,
        ),
        "has_gguf": any(
            isinstance(tag, str) and (tag.lower() == "gguf" or tag.lower() == "format:gguf")
            for tag in tags
        ),
    }


@router.get("/search")
async def search_catalog(
    request: Request,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=12, ge=1, le=MAX_RESULTS),
):
    """Search Hugging Face models with explicit external-service failures."""
    denied = _capability(request)
    if denied:
        return denied
    params: dict[str, Any] = {
        "limit": limit,
        "sort": "downloads",
        "direction": -1,
    }
    if q.strip():
        params["search"] = q.strip()
    else:
        params["pipeline_tag"] = "text-generation"
    try:
        models = await _get_json("/models", params)
        if not isinstance(models, list):
            raise ValueError("Unexpected catalog response")
        return {
            "models": [_catalog_item(m) for m in models if isinstance(m, dict)],
            "source": "huggingface",
            "note": (
                "Model metadata is remote. Installability and hardware fit "
                "are not yet verified."
            ),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "models": [],
            "source": "huggingface",
            "error": "Hugging Face is unavailable or returned an invalid response.",
            "detail_type": type(exc).__name__,
        }


@router.get("/models/{owner}/{name}")
async def model_details(request: Request, owner: str, name: str):
    denied = _capability(request)
    if denied:
        return denied
    identifier = f"{owner}/{name}"
    if not REPO_ID.fullmatch(identifier):
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Invalid Hugging Face repository ID.",
            get_request_id(request),
        )
    try:
        data = await _get_json(f"/models/{identifier}", {"blobs": "true"})
        if not isinstance(data, dict):
            raise ValueError("Unexpected model response")
        files = []
        for row in data.get("siblings") or []:
            filename = row.get("rfilename") if isinstance(row, dict) else None
            if not isinstance(filename, str) or not filename.lower().endswith(GGUF_SUFFIX):
                continue
            size = row.get("size")
            files.append({
                "filename": filename,
                "size_bytes": size if isinstance(size, int) and size >= 0 else None,
            })
        return {
            "model": _catalog_item(data),
            "gguf_files": files[:100],
            "file_count_truncated": len(files) > 100,
            "note": (
                "GGUF file sizes are weight-only; runtime overhead and "
                "KV cache require extra memory."
            ),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "model": {"id": identifier},
            "gguf_files": [],
            "error": "Could not fetch model metadata.",
            "detail_type": type(exc).__name__,
        }


class InstallGGUFBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=3, max_length=190)
    filename: str = Field(min_length=6, max_length=200)


@router.get("/installs")
async def list_installs(request: Request):
    denied = _capability(request)
    if denied:
        return denied
    return {
        "jobs": request.app.state.model_installs.public_jobs(),
        "note": "Job state is in-memory; installed files persist in the model volume.",
    }


@router.post("/installs", status_code=202)
async def install_gguf(body: InstallGGUFBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        job = await request.app.state.model_installs.start(body.repo, body.filename)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"job": job, "note": "Installation does not automatically load models."}


@router.post("/installs/{job_id}/cancel")
async def cancel_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    cancelled = await request.app.state.model_installs.cancel(job_id)
    return {"cancel_requested": cancelled}


@router.post("/installs/{job_id}/retry", status_code=202)
async def retry_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        job = await request.app.state.model_installs.retry(job_id)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"job": job}


@router.delete("/installs/{job_id}")
async def remove_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        removed = await request.app.state.model_installs.remove(job_id)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"removed": removed}
