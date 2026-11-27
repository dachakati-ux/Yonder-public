"""Read-only setup diagnostics scoped to the current chat principal."""

from __future__ import annotations

from fastapi import APIRouter, Request
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.routing.resolve import RouteResolutionError, resolve_route

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


@router.get("/status")
async def setup_status(request: Request):
    principal = get_principal(request)
    if not principal.has_capability("chat.use"):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks chat.use capability.",
            get_request_id(request),
        )
    state = request.app.state
    policy = state.settings.routing
    aliases = []
    for route in sorted(state.routes.list_enabled_aliases(), key=lambda route: route.alias):
        if (
            route.experimental
            and policy.require_capability_for_experimental
            and not principal.has_capability(policy.experimental_capability)
        ):
            continue
        deployment = state.routes.get_deployment(route.deployment_id)
        if deployment is None:
            continue
        definition = state.model_definitions.get(deployment.model_definition_id)
        if not definition or "chat" not in definition.capabilities:
            continue
        resolved = resolve_route(state.routes, route.alias)
        configured = state.providers.get(deployment.provider) is not None
        is_mock = (
            deployment.provider.startswith("mock")
            or deployment.runtime_configuration.get("lane") == "mock"
        )
        aliases.append(
            {
                "alias": route.alias,
                "kind": "mock" if is_mock else "model",
                "status": (
                    "unavailable"
                    if isinstance(resolved, RouteResolutionError) or not configured
                    else "route_ready"
                ),
                "experimental": route.experimental,
            }
        )
    # A snapshot is not proof of a successful future inference. Never expose
    # provider endpoints, filesystem paths or worker identities here.
    return {
        "security_mode": state.settings.security.mode.value,
        "gateway_ready": bool(getattr(state, "accepting_requests", False)),
        "aliases": aliases,
    }
