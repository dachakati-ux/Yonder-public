"""Authenticated Yonder add-on controller service. No public browser endpoints."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .engine import Controller, ControllerError

TOKEN_LENGTH = 32


class SetupValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict, max_length=24)


class RecoveryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_state: Literal["running", "stopped", "not_deployed"]


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addon_id: str = Field(min_length=2, max_length=64)
    action: Literal["install", "remove"]
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{12,128}$")


def create_app(
    *, config_path: Path | None = None, database_path: Path | None = None,
    token: str | None = None, controller: Controller | None = None,
) -> FastAPI:
    shared_token = token if token is not None else os.getenv("YONDER_CONTROLLER_TOKEN", "")
    if len(shared_token) < TOKEN_LENGTH or shared_token.strip() != shared_token:
        raise RuntimeError("Controller requires a generated token of at least 32 characters")
    engine = controller or Controller(
        config_path or Path(
            os.getenv("YONDER_CONTROLLER_DEPLOYMENTS", "/etc/yonder/deployments.json")
        ),
        database_path or Path(
            os.getenv("YONDER_CONTROLLER_DB", "/var/lib/yonder/controller.db")
        ),
    )
    # Fail closed on malformed or missing deployment allowlist.
    engine.approved()
    app = FastAPI(
        title="Yonder Add-on Controller", version="0.1.0",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not secrets.compare_digest(supplied, shared_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Controller authentication required",
            )

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "yonder-addon-controller"}

    @app.get("/v1/preflight/{addon_id}", dependencies=[Depends(authenticate)])
    async def preflight(addon_id: str):
        try:
            return await engine.preflight_report(addon_id)
        except ControllerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/setup/{addon_id}", dependencies=[Depends(authenticate)])
    async def get_setup(addon_id: str):
        try:
            return engine.setup(addon_id)
        except ControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/v1/setup/{addon_id}", dependencies=[Depends(authenticate)])
    async def save_setup(addon_id: str, body: SetupValues):
        try:
            return await engine.configure(addon_id, body.values)
        except ControllerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/approved", dependencies=[Depends(authenticate)])
    async def approved():
        return {
            "deployments": [
                {"addon_id": item.addon_id, "version": item.version, "adapter": "docker"}
                for item in engine.approved().values()
            ]
        }

    @app.get("/v1/plans/{addon_id}", dependencies=[Depends(authenticate)])
    async def plan(addon_id: str, action: Literal["install", "remove"] = "install"):
        try:
            result = engine.plan(addon_id, action)
        except ControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"plan": result}

    @app.get("/v1/inventory", dependencies=[Depends(authenticate)])
    async def inventory():
        return {"items": await engine.inventory(), "automatic_repair": False}

    @app.get("/v1/jobs", dependencies=[Depends(authenticate)])
    async def jobs():
        return {"jobs": engine.jobs.list()}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    async def get_job(job_id: str):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return {"job": job}

    @app.post("/v1/jobs", status_code=202, dependencies=[Depends(authenticate)])
    async def create_job(request: JobRequest):
        try:
            job, created = await engine.submit(
                request.addon_id, request.action,
                request.plan_sha256, request.idempotency_key,
            )
        except ControllerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job": job, "created": created}

    @app.get("/v1/observed/{addon_id}", dependencies=[Depends(authenticate)])
    async def observed(addon_id: str):
        try:
            value = await engine.observed(addon_id)
        except ControllerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"addon_id": addon_id, "observed_state": value}

    @app.post(
        "/v1/jobs/{job_id}/acknowledge", dependencies=[Depends(authenticate)]
    )
    async def acknowledge(job_id: str, review: RecoveryReview):
        try:
            job = await engine.acknowledge(job_id, review.observed_state)
        except ControllerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job": job}

    return app


# The app is created by Uvicorn using its factory mode.
