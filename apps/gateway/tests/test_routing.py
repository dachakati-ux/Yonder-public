"""Routing, admission size checks, and admission gate tests."""

from __future__ import annotations

import pytest
from app.routing import (
    DeploymentRecord,
    DeploymentState,
    ModelAliasRoute,
    RouteRegistry,
    RouteState,
    admit_request,
    resolve_route,
)
from app.routing.gate import DeploymentAdmissionGate, GateRejection
from app.routing.resolve import RouteResolutionError
from yonder_configuration.models import AdmissionConfig
from yonder_contracts.errors import ErrorCode


def _registry() -> RouteRegistry:
    routes = RouteRegistry()
    routes.upsert_deployment(
        DeploymentRecord(
            id="dep-1",
            model_definition_id="model-1",
            worker_id="worker-1",
            provider="mock",
            status=DeploymentState.READY,
        )
    )
    routes.upsert_alias(
        ModelAliasRoute(alias="yonder-chat", deployment_id="dep-1", state=RouteState.ENABLED)
    )
    return routes


def test_resolve_unknown_alias() -> None:
    result = resolve_route(_registry(), "missing")
    assert isinstance(result, RouteResolutionError)
    assert result.code == ErrorCode.UNKNOWN_MODEL


def test_resolve_ready_alias() -> None:
    result = resolve_route(_registry(), "yonder-chat")
    assert not isinstance(result, RouteResolutionError)
    assert result.deployment.id == "dep-1"


def test_admission_rejects_oversized_prompt() -> None:
    deployment = DeploymentRecord(
        id="dep-1",
        model_definition_id="model-1",
        worker_id="worker-1",
        provider="mock",
    )
    decision = admit_request(
        deployment=deployment,
        admission=AdmissionConfig(max_prompt_chars=10),
        prompt_chars=50,
        max_tokens=16,
    )
    assert decision.allowed is False
    assert decision.code == ErrorCode.INVALID_REQUEST


@pytest.mark.asyncio
async def test_gate_rejects_when_capacity_and_queue_full() -> None:
    deployment = DeploymentRecord(
        id="dep-1",
        model_definition_id="model-1",
        worker_id="worker-1",
        provider="mock",
        active_requests=1,
        queue_depth=1,
    )
    gate = DeploymentAdmissionGate(
        AdmissionConfig(
            max_active_requests_per_deployment=1,
            max_queue_depth=1,
            queue_timeout_seconds=0.05,
        )
    )
    with pytest.raises(GateRejection) as exc:
        await gate.acquire(deployment)
    assert exc.value.code == ErrorCode.CAPACITY_EXCEEDED


@pytest.mark.asyncio
async def test_gate_queue_timeout() -> None:
    deployment = DeploymentRecord(
        id="dep-1",
        model_definition_id="model-1",
        worker_id="worker-1",
        provider="mock",
        active_requests=1,
        queue_depth=0,
    )
    gate = DeploymentAdmissionGate(
        AdmissionConfig(
            max_active_requests_per_deployment=1,
            max_queue_depth=4,
            queue_timeout_seconds=0.05,
        )
    )

    async def waiter() -> None:
        await gate.acquire(deployment)

    with pytest.raises(GateRejection) as exc:
        await waiter()
    assert exc.value.code == ErrorCode.QUEUE_TIMEOUT
    assert deployment.queue_depth == 0
