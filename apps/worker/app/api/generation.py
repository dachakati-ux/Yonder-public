"""Worker-owned Diffusers generation endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1/generation", tags=["generation"])


class WorkerGenerationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    deployment_id: str | None = None
    model_alias: str | None = None
    mode: str
    prompt: str
    negative_prompt: str = ""
    params: dict = Field(default_factory=dict)


@router.post("/jobs")
async def accept_generation_job(
    body: WorkerGenerationJobRequest, request: Request
) -> dict[str, object]:
    manager = request.app.state.diffusers_runtime
    if manager.enabled or manager.dry_run:
        try:
            result = manager.create_job(
                job_id=body.job_id,
                deployment_id=body.deployment_id,
                mode=body.mode,
                prompt=body.prompt,
                negative_prompt=body.negative_prompt,
                params=dict(body.params),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**result, "worker_id": request.app.state.settings.worker_id}
    return {
        "accepted": False,
        "job_id": body.job_id,
        "status": "not_supported",
        "availability": "not_supported",
        "message": (
            "Worker Diffusers generation runtime is disabled."
        ),
        "worker_id": request.app.state.settings.worker_id,
    }


@router.get("/jobs/{job_id}")
async def get_generation_job(job_id: str, request: Request) -> dict[str, object]:
    manager = request.app.state.diffusers_runtime
    if manager.enabled or manager.dry_run:
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown generation job: {job_id}")
        return {**job, "worker_id": request.app.state.settings.worker_id}
    return {
        "job_id": job_id,
        "status": "not_supported",
        "availability": "not_supported",
        "worker_id": request.app.state.settings.worker_id,
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_generation_job(job_id: str, request: Request) -> dict[str, object]:
    manager = request.app.state.diffusers_runtime
    if not (manager.enabled or manager.dry_run):
        return {
            "job_id": job_id,
            "status": "not_supported",
            "availability": "not_supported",
            "worker_id": request.app.state.settings.worker_id,
        }
    job = manager.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown generation job: {job_id}")
    return {**job, "worker_id": request.app.state.settings.worker_id}


@router.get("/artifacts/{artifact_id}")
async def get_generation_artifact(artifact_id: str, request: Request):
    """Private Gateway-to-worker artifact fetch. Clients never call this."""
    manager = request.app.state.diffusers_runtime
    if not (manager.enabled or manager.dry_run):
        raise HTTPException(
            status_code=501,
            detail="Worker Diffusers generation runtime is disabled.",
        )
    meta = manager.resolve_artifact(artifact_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown artifact: {artifact_id}")
    path = Path(str(meta["path"]))
    return FileResponse(
        path,
        media_type=str(meta.get("mime_type") or "application/octet-stream"),
        filename=str(meta.get("filename") or path.name),
    )
