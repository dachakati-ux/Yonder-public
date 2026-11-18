"""Alias resolution and deployment selection."""

from __future__ import annotations

from dataclasses import dataclass

from yonder_contracts.errors import ErrorCode

from app.routing.models import DeploymentRecord, DeploymentState, ModelAliasRoute, RouteState
from app.routing.registry import RouteRegistry


@dataclass(frozen=True)
class ResolvedRoute:
    alias: ModelAliasRoute
    deployment: DeploymentRecord


@dataclass(frozen=True)
class RouteResolutionError:
    code: ErrorCode
    message: str
    details: dict[str, str]


def resolve_route(
    registry: RouteRegistry,
    model_alias: str,
) -> ResolvedRoute | RouteResolutionError:
    alias = registry.get_alias(model_alias)
    if alias is None:
        return RouteResolutionError(
            code=ErrorCode.UNKNOWN_MODEL,
            message=f"Unknown model alias: {model_alias}",
            details={"model": model_alias},
        )

    if alias.state == RouteState.DISABLED:
        return RouteResolutionError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message=f"Model alias is disabled: {model_alias}",
            details={"model": model_alias, "route_state": alias.state.value},
        )

    deployment = registry.get_deployment(alias.deployment_id)
    if deployment is None:
        return RouteResolutionError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message=f"No deployment registered for alias: {model_alias}",
            details={
                "model": model_alias,
                "deployment_id": alias.deployment_id,
            },
        )

    if deployment.maintenance:
        return RouteResolutionError(
            code=ErrorCode.WORKER_UNAVAILABLE,
            message="Deployment worker is in maintenance mode.",
            details={"deployment_id": deployment.id, "worker_id": deployment.worker_id},
        )

    if alias.state == RouteState.DRAINING or deployment.status == DeploymentState.DRAINING:
        return RouteResolutionError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="Model deployment is draining and not accepting new requests.",
            details={"deployment_id": deployment.id, "status": deployment.status.value},
        )

    if deployment.status == DeploymentState.LOADING:
        return RouteResolutionError(
            code=ErrorCode.MODEL_LOADING,
            message="Model deployment is still loading.",
            details={"deployment_id": deployment.id},
        )

    if deployment.status != DeploymentState.READY:
        return RouteResolutionError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="The requested model is not currently available.",
            details={
                "deployment_id": deployment.id,
                "status": deployment.status.value,
            },
        )

    if alias.state in {RouteState.UNAVAILABLE}:
        return RouteResolutionError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="Model route is marked unavailable.",
            details={"model": model_alias},
        )

    return ResolvedRoute(alias=alias, deployment=deployment)
