"""Administrative add-on catalog, installation and bundled extension routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
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
