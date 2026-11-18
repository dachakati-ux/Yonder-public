"""Administrative add-on catalog, installation and bundled extension routes."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.addon_controller_client import ControllerUnavailable
from app.services.addon_service import ServiceProbeError
from app.services.addons import AddonError

router = APIRouter(prefix="/api/admin/addons", tags=["addons"])


def _authorize(request: Request, capability: str):
    if get_principal(request).has_capability(capability):
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        f"Principal lacks {capability} capability.",
        get_request_id(request),
    )


def _audit(request: Request, addon_id: str, operation: str) -> None:
    request.app.state.event_store.append(
        {
            "event_type": f"addon.{operation}",
            "resource_type": "addon",
            "resource_id": addon_id,
            "principal_subject": get_principal(request).subject,
            "request_id": get_request_id(request),
            "result": "success",
        }
    )


@router.get("")
async def list_addons(request: Request):
    denied = _authorize(request, "operations.view")
    if denied:
        return denied
    registry = request.app.state.addons
    await registry.refresh_remote()
    return registry.snapshot()


@router.post("/refresh")
async def refresh_catalog(request: Request):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    registry = request.app.state.addons
    await registry.refresh_remote(force=True)
    return registry.snapshot()


@router.post("/{addon_id}/install")
async def install_addon(addon_id: str, request: Request):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    try:
        result = request.app.state.addons.install(addon_id)
    except (AddonError, OSError) as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            str(exc),
            get_request_id(request),
        )
    if result["changed"]:
        _audit(request, addon_id, "install")
    return {"installation": result, "catalog": request.app.state.addons.snapshot()}


@router.post("/{addon_id}/uninstall")
async def uninstall_addon(addon_id: str, request: Request):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    try:
        result = request.app.state.addons.uninstall(addon_id)
    except OSError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            f"Could not persist add-on state: {type(exc).__name__}",
            get_request_id(request),
        )
    if result["changed"]:
        _audit(request, addon_id, "uninstall")
    return {"installation": result, "catalog": request.app.state.addons.snapshot()}


@router.get("/security-insights/report")
async def security_insights(request: Request):
    denied = _authorize(request, "operations.view")
    if denied:
        return denied
    try:
        return request.app.state.addons.security_report(request.app.state.settings)
    except AddonError as exc:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            str(exc),
            get_request_id(request),
        )


@router.get("/{addon_id}/service/health")
async def addon_service_health(addon_id: str, request: Request):
    denied = _authorize(request, "operations.view")
    if denied:
        return denied
    registry = request.app.state.addons
    if addon_id not in {item["id"] for item in registry.snapshot()["addons"]}:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Unknown bundled add-on.",
            get_request_id(request),
        )
    return await request.app.state.addon_services.health(
        addon_id, installed=registry.installed(addon_id)
    )


@router.post("/services/reload")
async def reload_services(request: Request):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    try:
        result = request.app.state.addon_services.reload()
    except ServiceProbeError:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Add-on service configuration is invalid or insecure.",
            get_request_id(request),
        )
    _audit(request, "services", "config_reload")
    return result


class ControllerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["install", "remove"]
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{12,128}$")


def _controller(request: Request):
    return request.app.state.addon_controller


def _deployable(request: Request, addon_id: str) -> bool:
    registry = request.app.state.addons
    return registry.installed(addon_id) and any(
        item["id"] == addon_id
        and item["compatibility"] == "compatible"
        and item["status"] == "installed"
        and not item["requires_attention"]
        for item in registry.snapshot()["addons"]
    )


@router.get("/controller/status")
async def controller_status(request: Request):
    denied = _authorize(request, "operations.view")
    if denied:
        return denied
    client = _controller(request)
    if client is None:
        return {
            "configured": False,
            "deployments": [],
            "note": "Optional addon controller is not configured on this host.",
        }
    try:
        details = await client.request("GET", "/v1/approved")
    except ControllerUnavailable:
        return {"configured": True, "available": False, "deployments": []}
    return {"configured": True, "available": True, **details}


@router.get("/controller/jobs")
async def controller_jobs(request: Request):
    denied = _authorize(request, "operations.view")
    if denied:
        return denied
    client = _controller(request)
    if client is None:
        return {"configured": False, "jobs": []}
    try:
        return await client.request("GET", "/v1/jobs")
    except ControllerUnavailable:
        return error_response(
            ErrorCode.INVALID_REQUEST, "Add-on controller is unavailable.",
            get_request_id(request),
        )


@router.get("/{addon_id}/controller/plan")
async def controller_plan(
    addon_id: str, request: Request, action: Literal["install", "remove"] = "install"
):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    if not _deployable(request, addon_id):
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Install and validate the reviewed add-on before deploying.",
            get_request_id(request),
        )
    client = _controller(request)
    if client is None:
        return error_response(
            ErrorCode.INVALID_REQUEST, "Controller is not configured.",
            get_request_id(request),
        )
    try:
        return await client.request(
            "GET", f"/v1/plans/{addon_id}?action={action}"
        )
    except ControllerUnavailable:
        return error_response(
            ErrorCode.INVALID_REQUEST, "Could not validate the deployment plan.",
            get_request_id(request),
        )


@router.post("/{addon_id}/controller/jobs")
async def controller_create_job(
    addon_id: str, body: ControllerOperation, request: Request
):
    denied = _authorize(request, "operations.manage")
    if denied:
        return denied
    if not _deployable(request, addon_id):
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Add-on is not installed or not compatible.",
            get_request_id(request),
        )
    client = _controller(request)
    if client is None:
        return error_response(
            ErrorCode.INVALID_REQUEST, "Controller is not configured.",
            get_request_id(request),
        )
    try:
        result = await client.request(
            "POST", "/v1/jobs",
            {"addon_id": addon_id, **body.model_dump()},
        )
    except ControllerUnavailable:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Controller refused operation; refresh the plan and review its policy.",
            get_request_id(request),
        )
    _audit(request, addon_id, f"controller.{body.action}.requested")
    return result
