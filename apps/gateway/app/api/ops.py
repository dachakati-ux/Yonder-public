"""Gateway operational HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from yonder_contracts.versioning import SERVICE_VERSION

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "yonder-gateway",
        "environment": settings.environment.value,
    }


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    settings = request.app.state.settings
    # Report the real request-acceptance state (false during rehydrate and
    # shutdown drain) so load balancers stop routing to a draining gateway.
    accepting = bool(getattr(request.app.state, "accepting_requests", False))
    if not accepting:
        response.status_code = 503
    return {
        "ready": accepting,
        "service": "yonder-gateway",
        "security_mode": settings.security.mode.value,
        "development_security_warning": settings.security.mode.value == "development",
    }


@router.get("/version")
async def version() -> dict[str, str]:
    return {
        "service": "yonder-gateway",
        "version": SERVICE_VERSION,
    }


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    body, content_type = request.app.state.metrics.render()
    return PlainTextResponse(content=body.decode("utf-8"), media_type=content_type)
