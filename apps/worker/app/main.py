"""Yonder Worker Agent entrypoint."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from yonder_configuration import load_settings
from yonder_configuration.models import SecurityMode
from yonder_configuration.worker_auth import (
    configure_worker_auth,
    worker_token_matches,
)
from yonder_contracts.versioning import SERVICE_VERSION
from yonder_telemetry import MetricsRegistry, configure_logging, log_event

from app.api.generation import router as generation_router
from app.api.lifecycle import router as lifecycle_router
from app.api.runtime_proxy import router as runtime_proxy_router
from app.api.speech import router as speech_router
from app.hardware import collect_hardware_inventory, inventory_as_dict
from app.registration import (
    WorkerRegistrationRequired,
    disconnect_from_gateway,
    ensure_registered_heartbeat,
    register_with_gateway,
)
from app.registration.endpoint import resolve_worker_endpoint
from app.runtime import (
    DiffusersRuntimeManager,
    LlamaCppRuntimeManager,
    SpeechRuntimeManager,
)

# Operational probes stay reachable without the worker service token.
_AUTH_EXEMPT_PATHS = {"/health", "/ready", "/version", "/metrics"}


def _install_worker_auth(app: FastAPI) -> None:
    """Require the shared worker service token on all non-probe routes.

    Enforced only when security.worker_shared_secret is configured; production
    configuration validation refuses startup without it. This authenticates
    the gateway (and operators holding the secret) to the worker — it is not
    corporate user authentication, which stays upstream.
    """

    settings = app.state.settings
    if not (settings.security.worker_shared_secret or "").strip():
        return

    header_name = settings.security.worker_token_header

    @app.middleware("http")
    async def worker_auth_middleware(request: Request, call_next):
        if request.url.path in _AUTH_EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        presented = request.headers.get(header_name)
        if not worker_token_matches(request.app.state.settings.security, presented):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "worker_token_required",
                        "message": (
                            "Worker service token missing or invalid. Gateway "
                            f"calls must supply {header_name}."
                        ),
                    }
                },
            )
        return await call_next(request)


def _runtime_summary(app: FastAPI) -> dict[str, object]:
    llama = app.state.llama_runtime.list_deployments()
    diffusers = app.state.diffusers_runtime.list_deployments()
    speech = app.state.speech_runtime.list_deployments()
    active = [
        {**item, "runtime_family": "llama_cpp"} for item in llama
    ] + [
        {**item, "runtime_family": "diffusers"} for item in diffusers
    ] + [
        {**item, "runtime_family": "speech"} for item in speech
    ]
    return {
        "status": "available",
        "deployments": active,
        "occupancy_source": "worker_runtime_managers",
    }


async def _heartbeat_loop(app: FastAPI) -> None:
    settings = app.state.settings
    logger = app.state.logger
    while True:
        try:
            hardware = inventory_as_dict()
            app.state.latest_hardware = hardware
            endpoint = resolve_worker_endpoint(settings)
            await ensure_registered_heartbeat(
                gateway_url=settings.gateway_url,
                worker_id=settings.worker_id,
                display_name=settings.worker_display_name,
                endpoint=endpoint,
                hardware=hardware,
                ready=True,
                runtime=_runtime_summary(app),
            )
            app.state.gateway_connected = True
            app.state.last_heartbeat_at = datetime.now(UTC).isoformat()
            log_event(logger, "worker.heartbeat.sent", worker_id=settings.worker_id)
        except WorkerRegistrationRequired as exc:
            app.state.gateway_connected = False
            log_event(
                logger,
                "worker.reregistration.failed",
                level=logging.WARNING,
                worker_id=settings.worker_id,
                status="error",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            app.state.gateway_connected = False
            log_event(
                logger,
                "worker.heartbeat.failed",
                level=logging.WARNING,
                worker_id=settings.worker_id,
                status="error",
                error=str(exc),
            )
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    logger = app.state.logger
    hardware = inventory_as_dict()
    app.state.latest_hardware = hardware
    endpoint = resolve_worker_endpoint(settings)

    try:
        await register_with_gateway(
            gateway_url=settings.gateway_url,
            worker_id=settings.worker_id,
            display_name=settings.worker_display_name,
            endpoint=endpoint,
            hardware=hardware,
            ready=True,
            runtime=_runtime_summary(app),
        )
        app.state.gateway_connected = True
        log_event(
            logger,
            "worker.registered",
            worker_id=settings.worker_id,
            endpoint=endpoint,
        )
    except Exception as exc:  # noqa: BLE001
        app.state.gateway_connected = False
        log_event(
            logger,
            "worker.registration.failed",
            level=logging.WARNING,
            worker_id=settings.worker_id,
            error=str(exc),
        )

    task = asyncio.create_task(_heartbeat_loop(app))
    app.state.heartbeat_task = task
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await disconnect_from_gateway(
                gateway_url=settings.gateway_url,
                worker_id=settings.worker_id,
            )
            log_event(logger, "worker.disconnected", worker_id=settings.worker_id)
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                "worker.disconnect.failed",
                level=logging.WARNING,
                worker_id=settings.worker_id,
                error=str(exc),
            )


def create_app() -> FastAPI:
    settings = load_settings()
    logger = configure_logging(
        service="yonder-worker",
        level=settings.logging.level,
        json_logs=settings.logging.json_logs,
    )

    if settings.security.mode == SecurityMode.DEVELOPMENT:
        print(
            "\n*** DEVELOPMENT SECURITY MODE — worker agent ***\n",
            file=sys.stderr,
        )

    app = FastAPI(
        title="Yonder Worker Agent",
        version=SERVICE_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.logger = logger
    app.state.metrics = MetricsRegistry(service="yonder-worker")
    app.state.latest_hardware = None
    app.state.lifecycle_deployments = {}
    app.state.gateway_connected = False
    app.state.last_heartbeat_at = None
    configure_worker_auth(settings.security)
    _install_worker_auth(app)
    app.state.llama_runtime = LlamaCppRuntimeManager(settings.llama_cpp_runtime)
    app.state.diffusers_runtime = DiffusersRuntimeManager(settings.diffusers_runtime)
    app.state.speech_runtime = SpeechRuntimeManager(settings.speech_runtime)
    # Auto-load configured speech models when present (in-memory; honest READY).
    speech = app.state.speech_runtime
    if speech.enabled and not speech.dry_run:
        try:
            cfg = settings.speech_runtime
            if cfg.whisper_model_path and Path(cfg.whisper_model_path).exists():
                speech.load(
                    deployment_id="speech-stt-boot",
                    kind="stt",
                    model_path=str(cfg.whisper_model_path),
                )
            if cfg.piper_model_path and Path(cfg.piper_model_path).exists():
                speech.load(
                    deployment_id="speech-tts-boot",
                    kind="tts",
                    model_path=str(cfg.piper_model_path),
                )
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                "worker.speech.autoload.failed",
                level=logging.WARNING,
                error=str(exc),
            )
    app.include_router(lifecycle_router)
    app.include_router(generation_router)
    app.include_router(runtime_proxy_router)
    app.include_router(speech_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "yonder-worker"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, object]:
        inventory = request.app.state.latest_hardware
        return {
            "ready": True,
            "service": "yonder-worker",
            "worker_id": settings.worker_id,
            "gpu_discovery_status": (
                inventory.get("gpu_discovery_status") if inventory else "not_reported"
            ),
        }

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"service": "yonder-worker", "version": SERVICE_VERSION}

    @app.get("/metrics")
    async def metrics(request: Request) -> PlainTextResponse:
        body, content_type = request.app.state.metrics.render()
        return PlainTextResponse(content=body.decode("utf-8"), media_type=content_type)

    @app.get("/v1/hardware")
    async def hardware() -> dict[str, object]:
        inventory = collect_hardware_inventory()
        return inventory.model_dump(mode="json")

    @app.get("/v1/status")
    async def status(request: Request) -> dict[str, object]:
        manager = request.app.state.llama_runtime
        diffusers_manager = request.app.state.diffusers_runtime
        runtime_active = bool(
            manager.enabled or settings.llama_cpp_runtime.dry_run
        )
        runtime_status = "llama_cpp" if runtime_active else "skeleton"
        runtime_message = (
            "Worker-managed llama.cpp runtime controller enabled"
            if runtime_active
            else "Inference provider adapters are not connected until Phase 3"
        )
        heartbeat_at = request.app.state.last_heartbeat_at
        return {
            "worker_id": settings.worker_id,
            "display_name": settings.worker_display_name,
            # Honest connection state: reflects the most recent registration
            # or heartbeat outcome instead of a hardcoded True.
            "connected_to_gateway": bool(request.app.state.gateway_connected),
            "last_heartbeat_at": heartbeat_at,
            "ready": True,
            "maintenance": False,
            "agent_version": SERVICE_VERSION,
            "latest_hardware": request.app.state.latest_hardware,
            "runtime": {
                "status": runtime_status,
                "message": runtime_message,
                "lifecycle_deployments": _runtime_summary(request.app)["deployments"]
                or list(request.app.state.lifecycle_deployments.values()),
                "diffusers_enabled": bool(
                    diffusers_manager.enabled or diffusers_manager.dry_run
                ),
            },
        }

    return app


def run() -> None:
    settings = load_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.worker.host,
        port=settings.worker.port,
        log_level=settings.logging.level.lower(),
    )


app = create_app()
