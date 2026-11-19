"""OIDC Authorization Code + PKCE browser login/callback/logout endpoints (M03.2/M03.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.context import get_principal
from app.services.oidc import PROVIDER_TEMPLATES, OidcBridge, OidcError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _bridge(request: Request) -> OidcBridge:
    bridge = getattr(request.app.state, "oidc_bridge", None)
    if bridge is None:
        raise HTTPException(
            status_code=404,
            detail="OIDC authentication is not active in the current security mode.",
        )
    return bridge


@router.get("/providers")
async def list_provider_templates(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "security_mode": settings.security.mode.value,
        "active_template": settings.security.oidc.provider_template,
        "templates": PROVIDER_TEMPLATES,
    }


@router.get("/login")
async def oidc_login(request: Request, mode: str = "redirect"):
    bridge = _bridge(request)
    try:
        authorization_url, tx_cookie = await bridge.build_login_redirect()
    except OidcError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    secure_cookie = not request.app.state.settings.security.oidc.allow_insecure_http_in_dev
    if mode == "json" or "application/json" in (request.headers.get("accept") or ""):
        response: JSONResponse | RedirectResponse = JSONResponse(
            {"authorization_url": authorization_url}
        )
    else:
        response = RedirectResponse(url=authorization_url, status_code=302)
    response.set_cookie(
        key=OidcBridge.TX_COOKIE_NAME,
        value=tx_cookie,
        max_age=OidcBridge.TX_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/v1/auth",
    )
    return response


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        raise HTTPException(status_code=401, detail=f"IdP returned error: {error}")
    bridge = _bridge(request)
    tx_cookie = request.cookies.get(OidcBridge.TX_COOKIE_NAME, "")
    if not tx_cookie:
        raise HTTPException(status_code=401, detail="Missing OIDC transaction cookie.")
    try:
        session_cookie, session_payload = await bridge.exchange_and_verify(
            code=code, state=state, tx_cookie=tx_cookie
        )
    except OidcError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    oidc_cfg = request.app.state.settings.security.oidc
    secure_cookie = not oidc_cfg.allow_insecure_http_in_dev
    response = JSONResponse(
        {
            "authenticated": True,
            "subject": session_payload["sub"],
            "roles": session_payload["roles"],
            "groups": session_payload["groups"],
            "capabilities": session_payload["capabilities"],
            "csrf_token": session_payload["csrf"],
        }
    )
    response.delete_cookie(key=OidcBridge.TX_COOKIE_NAME, path="/v1/auth")
    response.set_cookie(
        key=oidc_cfg.session_cookie_name,
        value=session_cookie,
        max_age=oidc_cfg.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    return response


@router.post("/logout")
async def oidc_logout(request: Request) -> JSONResponse:
    oidc_cfg = request.app.state.settings.security.oidc
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(key=oidc_cfg.session_cookie_name, path="/")
    response.delete_cookie(key=OidcBridge.TX_COOKIE_NAME, path="/v1/auth")
    return response


@router.get("/session")
async def oidc_session_info(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    return {
        "authenticated": True,
        "subject": principal.subject,
        "principal_type": principal.principal_type,
        "roles": list(principal.roles),
        "groups": list(principal.groups),
        "capabilities": list(principal.capabilities),
        "authentication_context_id": principal.authentication_context_id,
        "csrf_token": principal.attributes.get("csrf_token"),
    }
