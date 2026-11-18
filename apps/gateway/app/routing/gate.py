"""Per-deployment admission gate with bounded queue and timeouts."""

from __future__ import annotations

import asyncio

from yonder_configuration.models import AdmissionConfig
from yonder_contracts.errors import ErrorCode

from app.routing.models import DeploymentRecord


class GateRejection(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class DeploymentAdmissionGate:
    """Enforces max active requests and a bounded wait queue per deployment."""

    def __init__(self, admission: AdmissionConfig) -> None:
        self._admission = admission
        self._conditions: dict[str, asyncio.Condition] = {}

    def _condition(self, deployment_id: str) -> asyncio.Condition:
        if deployment_id not in self._conditions:
            self._conditions[deployment_id] = asyncio.Condition()
        return self._conditions[deployment_id]

    async def acquire(self, deployment: DeploymentRecord) -> None:
        condition = self._condition(deployment.id)
        async with condition:
            if deployment.active_requests >= self._admission.max_active_requests_per_deployment:
                if deployment.queue_depth >= self._admission.max_queue_depth:
                    raise GateRejection(
                        code=ErrorCode.CAPACITY_EXCEEDED,
                        message="Deployment is at capacity.",
                        details={
                            "deployment_id": deployment.id,
                            "active_requests": str(deployment.active_requests),
                            "queue_depth": str(deployment.queue_depth),
                        },
                    )
                deployment.queue_depth += 1
                try:
                    try:
                        await asyncio.wait_for(
                            condition.wait_for(
                                lambda: (
                                    deployment.active_requests
                                    < self._admission.max_active_requests_per_deployment
                                )
                            ),
                            timeout=self._admission.queue_timeout_seconds,
                        )
                    except TimeoutError as exc:
                        raise GateRejection(
                            code=ErrorCode.QUEUE_TIMEOUT,
                            message="Timed out waiting for deployment capacity.",
                            details={
                                "deployment_id": deployment.id,
                                "queue_timeout_seconds": str(
                                    self._admission.queue_timeout_seconds
                                ),
                            },
                        ) from exc
                finally:
                    deployment.queue_depth = max(0, deployment.queue_depth - 1)

            deployment.active_requests += 1
            deployment.request_count += 1

    async def release(self, deployment: DeploymentRecord) -> None:
        from datetime import UTC, datetime

        condition = self._condition(deployment.id)
        async with condition:
            deployment.active_requests = max(0, deployment.active_requests - 1)
            if deployment.active_requests == 0:
                deployment.last_released_at = datetime.now(UTC)
            condition.notify_all()
