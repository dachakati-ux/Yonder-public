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
from app.services.model_discovery import ModelDiscoveryError
from app.services.model_library_ops import (
    BenchmarkStore,
    ModelRegistrationStore,
    build_compatibility_matrix,
    evaluate_heterogeneous_placement,
    is_model_library_enabled,
    reschedule_failed_deployment,
)

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


def _require_model_library(request: Request):
    if is_model_library_enabled():
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        (
            "Optional Model Library module is disabled; set "
            "YONDER_MODEL_LIBRARY_ENABLED=true to enable catalog search and downloads."
        ),
        get_request_id(request),
        status_code=403,
        details={"reason": "MODEL_LIBRARY_DISABLED"},
    )


def _registration_store(request: Request) -> ModelRegistrationStore:
    store = getattr(request.app.state, "model_registrations", None)
    if store is None:
        store = ModelRegistrationStore()
        request.app.state.model_registrations = store
    return store


def _benchmark_store(request: Request) -> BenchmarkStore:
    store = getattr(request.app.state, "model_benchmarks", None)
    if store is None:
        store = BenchmarkStore()
        request.app.state.model_benchmarks = store
    return store


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
        "revision": item.get("sha"),
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


def estimate_gguf_memory_fit(
    size_bytes: int | None,
    *,
    available_vram_mb: int | None = None,
    available_ram_mb: int | None = None,
) -> dict[str, Any]:
    """Conservative weight + KV-cache estimate; explicitly marked as estimate."""
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return {"fit": "unknown", "estimated_required_mb": None, "source": "unavailable"}
    weight_mb = size_bytes / (1024 * 1024)
    estimated_required_mb = int(round(weight_mb * 1.2 + 512))
    if available_vram_mb is not None and estimated_required_mb <= available_vram_mb:
        fit = "gpu_fit"
    elif available_ram_mb is not None and estimated_required_mb <= available_ram_mb:
        fit = "cpu_ram_fit"
    elif available_vram_mb is not None or available_ram_mb is not None:
        fit = "exceeds_memory"
    else:
        fit = "unknown"
    return {
        "fit": fit,
        "estimated_required_mb": estimated_required_mb,
        "source": "heuristic_1_2x_plus_512mb",
    }


EXTERNAL_RUNTIME_PRESETS: dict[str, dict[str, str]] = {
    "lm_studio": {
        "label": "LM Studio (macOS / Windows / Linux)",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "docker_base_url": "http://host.docker.internal:1234/v1",
        "protocol": "openai_compatible",
    },
    "mlx_lm": {
        "label": "MLX LM Server (Apple Silicon)",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "docker_base_url": "http://host.docker.internal:8080/v1",
        "protocol": "openai_compatible",
    },
    "ollama": {
        "label": "Ollama (OpenAI-compatible endpoint)",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "docker_base_url": "http://host.docker.internal:11434/v1",
        "protocol": "openai_compatible",
    },
    "llama_cpp": {
        "label": "llama.cpp llama-server",
        "default_base_url": "http://127.0.0.1:8081/v1",
        "docker_base_url": "http://host.docker.internal:8081/v1",
        "protocol": "openai_compatible",
    },
}


@router.get("/runtime-presets")
async def runtime_presets(request: Request):
    denied = _capability(request)
    if denied:
        return denied
    return {"presets": EXTERNAL_RUNTIME_PRESETS}


@router.get("/search")
async def search_catalog(
    request: Request,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=12, ge=1, le=MAX_RESULTS),
    license_filter: str = Query(default="", max_length=64),
    gguf_only: bool = Query(default=False),
    exclude_gated: bool = Query(default=True),
):
    """Search Hugging Face models with explicit external-service failures."""
    denied = _capability(request) or _require_model_library(request)
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
        items = [_catalog_item(m) for m in models if isinstance(m, dict)]
        if exclude_gated:
            items = [m for m in items if not m["gated"] and not m["private"]]
        if gguf_only:
            items = [m for m in items if m["has_gguf"]]
        if license_filter.strip():
            wanted = license_filter.strip().lower()
            items = [m for m in items if (m["license"] or "").lower() == wanted]
        return {
            "models": items,
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
async def model_details(
    request: Request,
    owner: str,
    name: str,
    available_vram_mb: int | None = Query(default=None, ge=1),
    available_ram_mb: int | None = Query(default=None, ge=1),
):
    denied = _capability(request) or _require_model_library(request)
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
            valid_size = size if isinstance(size, int) and size >= 0 else None
            files.append({
                "filename": filename,
                "size_bytes": valid_size,
                "memory_fit": estimate_gguf_memory_fit(
                    valid_size,
                    available_vram_mb=available_vram_mb,
                    available_ram_mb=available_ram_mb,
                ),
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
    denied = _capability(request) or _require_model_library(request)
    if denied:
        return denied
    return {
        "jobs": request.app.state.model_installs.public_jobs(),
        "note": "Job state is in-memory; installed files persist in the model volume.",
    }


@router.post("/installs", status_code=202)
async def install_gguf(body: InstallGGUFBody, request: Request):
    denied = _capability(request, "models.manage") or _require_model_library(request)
    if denied:
        return denied
    try:
        job = await request.app.state.model_installs.start(body.repo, body.filename)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"job": job, "note": "Installation does not automatically load models."}


@router.post("/installs/{job_id}/cancel")
async def cancel_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage") or _require_model_library(request)
    if denied:
        return denied
    cancelled = await request.app.state.model_installs.cancel(job_id)
    return {"cancel_requested": cancelled}


@router.post("/installs/{job_id}/retry", status_code=202)
async def retry_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage") or _require_model_library(request)
    if denied:
        return denied
    try:
        job = await request.app.state.model_installs.retry(job_id)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"job": job}


@router.delete("/installs/{job_id}")
async def remove_install(job_id: str, request: Request):
    denied = _capability(request, "models.manage") or _require_model_library(request)
    if denied:
        return denied
    try:
        removed = await request.app.state.model_installs.remove(job_id)
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"removed": removed}


class RegisterBundleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=512)
    runtime_id: str = Field(min_length=1, max_length=64)
    modality: str = Field(default="chat", max_length=32)
    simulate_load_failure: bool = False


class RollbackBundleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="operator_rollback", max_length=200)


class PlacementRecommendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_vram_gb: float = Field(gt=0, le=2048)
    preferred_worker_id: str | None = Field(default=None, max_length=128)
    supported_runtimes: list[str] = Field(default_factory=list)


class PlacementRescheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failed_worker_id: str = Field(min_length=1, max_length=128)
    required_vram_gb: float = Field(gt=0, le=2048)
    supported_runtimes: list[str] = Field(default_factory=list)


class BenchmarkRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=128)
    runtime_id: str = Field(min_length=1, max_length=64)
    worker_id: str = Field(min_length=1, max_length=128)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: float = Field(gt=0)
    seed: int = Field(default=42)
    parameters: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
async def catalog_status(request: Request):
    denied = _capability(request)
    if denied:
        return denied
    return {
        "model_library_enabled": is_model_library_enabled(),
        "core_connections_enabled": True,
        "preset_count": len(EXTERNAL_RUNTIME_PRESETS),
    }


@router.get("/bundles/registrations")
async def list_bundle_registrations(request: Request):
    denied = _capability(request)
    if denied:
        return denied
    return {"registrations": _registration_store(request).list_registrations()}


@router.post("/bundles/register")
async def register_bundle(body: RegisterBundleBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        record = _registration_store(request).validate_and_register(
            model_id=body.model_id,
            raw_path=body.path,
            model_roots=request.app.state.settings.model_roots,
            runtime_id=body.runtime_id,
            modality=body.modality,
            simulate_load_failure=body.simulate_load_failure,
        )
    except ModelDiscoveryError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            exc.message,
            get_request_id(request),
            details={"reason": exc.code},
        )
    return {"registration": record.to_dict()}


@router.post("/bundles/{model_id}/rollback")
async def rollback_bundle(model_id: str, body: RollbackBundleBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        result = _registration_store(request).rollback(model_id, reason=body.reason)
    except ModelDiscoveryError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            exc.message,
            get_request_id(request),
            details={"reason": exc.code},
        )
    return result


@router.post("/placement/recommend")
async def recommend_placement(body: PlacementRecommendBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    workers = getattr(request.app.state, "workers", {})
    return evaluate_heterogeneous_placement(
        workers,
        required_vram_gb=body.required_vram_gb,
        preferred_worker_id=body.preferred_worker_id,
        supported_runtimes=set(body.supported_runtimes) if body.supported_runtimes else None,
    )


@router.post("/placement/reschedule")
async def reschedule_placement(body: PlacementRescheduleBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    workers = getattr(request.app.state, "workers", {})
    return reschedule_failed_deployment(
        workers,
        failed_worker_id=body.failed_worker_id,
        required_vram_gb=body.required_vram_gb,
        supported_runtimes=set(body.supported_runtimes) if body.supported_runtimes else None,
    )


@router.get("/compatibility-matrix")
async def compatibility_matrix(request: Request):
    denied = _capability(request)
    if denied:
        return denied
    workers = getattr(request.app.state, "workers", {})
    return {"matrix": build_compatibility_matrix(EXTERNAL_RUNTIME_PRESETS, workers)}


@router.get("/benchmarks")
async def list_benchmarks(request: Request, model_id: str | None = Query(default=None)):
    denied = _capability(request)
    if denied:
        return denied
    return {"runs": _benchmark_store(request).list_runs(model_id=model_id)}


@router.post("/benchmarks")
async def record_benchmark(body: BenchmarkRunBody, request: Request):
    denied = _capability(request, "models.manage")
    if denied:
        return denied
    try:
        run = _benchmark_store(request).record_run(
            model_id=body.model_id,
            runtime_id=body.runtime_id,
            worker_id=body.worker_id,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            latency_ms=body.latency_ms,
            seed=body.seed,
            parameters=body.parameters,
        )
    except ValueError as exc:
        return error_response(ErrorCode.INVALID_REQUEST, str(exc), get_request_id(request))
    return {"run": run}
