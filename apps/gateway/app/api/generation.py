"""Gateway-only generation job and artifact APIs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode
from yonder_contracts.generation import GenerationMode

from app.context import get_principal, get_request_id
from app.errors import error_response

router = APIRouter(prefix="/v1/generation", tags=["generation"])


class CreateGenerationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: GenerationMode = GenerationMode.TEXT_TO_IMAGE
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = ""
    aspect_ratio: str = "1:1"
    style_preset: str | None = None
    steps: int = Field(default=20, ge=1, le=150)
    guidance: float = Field(default=7.5, ge=0.0, le=50.0)
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int | None = None
    reference_artifact_ids: list[str] = Field(default_factory=list)
    model: str | None = None


@router.post("/uploads")
async def upload_reference(
    request: Request,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "reference",
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    data = await file.read()
    mime = file.content_type or "application/octet-stream"
    try:
        stored = request.app.state.artifact_store.put(
            data=data,
            filename=file.filename or "upload.bin",
            mime_type=mime,
            kind=kind,
        )
    except ValueError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            str(exc),
            request_id,
            status_code=400,
        )
    meta = {
        "id": stored.artifact_id,
        "principal_subject": principal.subject,
        "kind": kind,
        "mime_type": stored.mime_type,
        "filename": stored.filename,
        "byte_size": stored.byte_size,
        "sha256": stored.sha256,
        "storage_key": stored.storage_key,
        "url": f"/v1/generation/artifacts/{stored.artifact_id}",
    }
    request.app.state.generation_jobs.register_artifact_meta(meta)
    return {
        "artifact": {
            "id": stored.artifact_id,
            "kind": kind,
            "mime_type": stored.mime_type,
            "filename": stored.filename,
            "byte_size": stored.byte_size,
            "sha256": stored.sha256,
            "url": meta["url"],
        }
    }


@router.post("/jobs")
async def create_job(
    body: CreateGenerationJobRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    try:
        job = await request.app.state.generation_jobs.create_job(
            principal_subject=principal.subject,
            request_id=request_id,
            mode=body.mode,
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            aspect_ratio=body.aspect_ratio,
            style_preset=body.style_preset,
            steps=body.steps,
            guidance=body.guidance,
            strength=body.strength,
            seed=body.seed,
            reference_artifact_ids=body.reference_artifact_ids,
            model_alias=body.model
            or (
                "yonder-video"
                if body.mode
                in {GenerationMode.TEXT_TO_VIDEO, GenerationMode.IMAGE_TO_VIDEO}
                else "yonder-image"
            ),
        )
    except PermissionError as exc:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            str(exc),
            request_id,
            status_code=403,
        )
    if job.get("status") == "queued":
        background_tasks.add_task(
            request.app.state.generation_jobs.run_accepted_job, job["id"]
        )
    return {"job": job}


@router.get("/jobs")
async def list_jobs(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    return {
        "jobs": await request.app.state.generation_jobs.list_jobs(
            principal_subject=principal.subject
        )
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    try:
        job = await request.app.state.generation_jobs.get_job(
            job_id, principal_subject=principal.subject
        )
    except PermissionError:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Job not visible to this principal.",
            request_id,
            status_code=403,
        )
    if job is None:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Unknown generation job.",
            request_id,
            status_code=404,
            details={"job_id": job_id},
        )
    return {"job": job}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    try:
        job = await request.app.state.generation_jobs.cancel_job(
            job_id, principal_subject=principal.subject
        )
    except KeyError:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Unknown generation job.",
            request_id,
            status_code=404,
        )
    except PermissionError:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Job not visible to this principal.",
            request_id,
            status_code=403,
        )
    return {"job": job}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    try:
        await request.app.state.generation_jobs.delete_job(
            job_id, principal_subject=principal.subject
        )
    except KeyError:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Unknown generation job.",
            request_id,
            status_code=404,
        )
    except PermissionError:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Job not visible to this principal.",
            request_id,
            status_code=403,
        )
    return {"deleted": True, "job_id": job_id}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, request: Request) -> Response:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not principal.has_capability("generation.use") and not principal.has_capability(
        "lab.use"
    ):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks generation.use capability.",
            request_id,
        )
    meta = request.app.state.generation_jobs.get_artifact_meta(artifact_id)
    if meta is None or meta.get("principal_subject") != principal.subject:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Artifact not found or not visible.",
            request_id,
            status_code=404,
            details={"artifact_id": artifact_id},
        )
    try:
        data = request.app.state.artifact_store.read(meta["storage_key"])
    except FileNotFoundError:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Artifact bytes missing from store.",
            request_id,
            status_code=404,
        )
    return Response(content=data, media_type=meta["mime_type"])
