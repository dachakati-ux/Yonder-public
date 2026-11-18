"""Mock generation provider — returns not_supported until a real runtime is wired.

Jobs are recorded so the Gateway job API and Studio can exercise durable flows
without pretending a GPU diffusion/video stack is available.
"""

from __future__ import annotations

from datetime import UTC, datetime

from yonder_contracts.generation import (
    GenerationJob,
    GenerationJobStatus,
    GenerationProgress,
    GenerationProvider,
    GenerationRequest,
)
from yonder_contracts.inference import ProviderHealth, ProviderHealthStatus


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MockGenerationProvider:
    """Explicitly mock/not_supported generation adapter for contract conformance."""

    name = "mock-generation"

    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            status=ProviderHealthStatus.HEALTHY,
            provider=self.name,
            message=(
                "Mock generation provider — real image/video runtime not selected "
                "(pending V100 host evidence)"
            ),
            details={"availability": "mock", "runtime": "not_configured"},
        )

    async def create_job(self, request: GenerationRequest) -> GenerationJob:
        job_id = f"mock-gen-{request.request_id}"
        now = _now()
        job = GenerationJob(
            job_id=job_id,
            request_id=request.request_id,
            status=GenerationJobStatus.NOT_SUPPORTED,
            mode=request.mode,
            model_alias=request.model_alias,
            deployment_id=request.deployment_id,
            provider=self.name,
            availability="not_supported",
            progress=(
                GenerationProgress(
                    job_id=job_id,
                    step="policy",
                    message="Request accepted by Gateway",
                    percent=5.0,
                ),
                GenerationProgress(
                    job_id=job_id,
                    step="runtime",
                    message=(
                        "No generation runtime configured. "
                        "Complete docs/v100-bootstrap.md before selecting a provider."
                    ),
                    percent=100.0,
                ),
            ),
            assets=(),
            error="generation_runtime_not_configured",
            created_at=now,
            updated_at=now,
            metadata={"provider": self.name, "availability": "not_supported"},
        )
        self._jobs[job_id] = job
        return job

    async def get_job(self, job_id: str) -> GenerationJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown generation job: {job_id}")
        return job

    async def cancel_job(self, job_id: str) -> GenerationJob:
        job = await self.get_job(job_id)
        if job.status in {
            GenerationJobStatus.SUCCEEDED,
            GenerationJobStatus.FAILED,
            GenerationJobStatus.CANCELLED,
            GenerationJobStatus.NOT_SUPPORTED,
            GenerationJobStatus.REJECTED,
        }:
            return job
        cancelled = GenerationJob(
            job_id=job.job_id,
            request_id=job.request_id,
            status=GenerationJobStatus.CANCELLED,
            mode=job.mode,
            model_alias=job.model_alias,
            deployment_id=job.deployment_id,
            provider=job.provider,
            availability=job.availability,
            progress=job.progress,
            assets=job.assets,
            error=job.error,
            created_at=job.created_at,
            updated_at=_now(),
            metadata=job.metadata,
        )
        self._jobs[job_id] = cancelled
        return cancelled


# Protocol conformance hint for type checkers
_: type[GenerationProvider] = MockGenerationProvider
