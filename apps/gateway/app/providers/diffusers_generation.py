"""Gateway adapter for worker-owned Diffusers generation jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from yonder_configuration.models import DiffusersProviderConfig
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.generation import (
    GenerationAssetRef,
    GenerationJob,
    GenerationJobStatus,
    GenerationMode,
    GenerationProgress,
    GenerationProvider,
    GenerationRequest,
    MediaKind,
)
from yonder_contracts.inference import (
    Deployment,
    DeploymentSpec,
    ProviderHealth,
    ProviderHealthStatus,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DiffusersGenerationProvider:
    """Submit and poll generation work through the worker HTTP boundary."""

    name = "diffusers"

    def __init__(
        self,
        config: DiffusersProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._requests: dict[str, GenerationRequest] = {}
        self._deployment_endpoints: dict[str, str] = {}
        self._job_deployments: dict[str, str] = {}

    def bind_job_route(
        self,
        job_id: str,
        *,
        deployment_id: str | None,
        worker_base_url: str | None = None,
    ) -> None:
        """Remember which worker owns a job so polls survive Gateway restarts."""
        if deployment_id:
            if job_id:
                self._job_deployments[job_id] = deployment_id
            if worker_base_url:
                self._deployment_endpoints[deployment_id] = worker_base_url.rstrip("/")

    def _endpoint_for_job(self, job_id: str) -> str:
        deployment_id = self._job_deployments.get(job_id)
        request = self._requests.get(job_id)
        if deployment_id is None and request is not None:
            deployment_id = request.deployment_id
        if deployment_id:
            endpoint = self._deployment_endpoints.get(deployment_id)
            if endpoint:
                return endpoint
        return self._config.worker_base_url

    def _client(self, base_url: str | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=(base_url or self._config.worker_base_url).rstrip("/"),
            timeout=self._config.request_timeout_seconds,
            transport=self._transport,
            headers=worker_auth_headers(),
        )

    async def load(self, deployment: DeploymentSpec) -> Deployment:
        endpoint = (deployment.worker_endpoint or self._config.worker_base_url).rstrip("/")
        self._deployment_endpoints[deployment.deployment_id] = endpoint
        return Deployment(
            deployment_id=deployment.deployment_id,
            status="ready",
            provider=self.name,
            details={
                "worker_base_url": endpoint,
                "runtime_lifecycle": "worker_managed",
            },
        )

    async def unload(self, deployment_id: str) -> None:
        self._deployment_endpoints.pop(deployment_id, None)

    async def health(self) -> ProviderHealth:
        try:
            async with self._client() as client:
                response = await client.get("/health")
                response.raise_for_status()
            return ProviderHealth(
                status=ProviderHealthStatus.HEALTHY,
                provider=self.name,
                message="Diffusers worker endpoint is reachable",
                details={
                    "availability": "available",
                    "worker_base_url": self._config.worker_base_url,
                },
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderHealth(
                status=ProviderHealthStatus.UNHEALTHY,
                provider=self.name,
                message="Diffusers worker endpoint is unavailable",
                details={
                    "availability": "collection_failed",
                    "worker_base_url": self._config.worker_base_url,
                    "error_type": type(exc).__name__,
                },
            )

    async def create_job(self, request: GenerationRequest) -> GenerationJob:
        job_id = f"diffusers-{request.request_id}"
        payload = {
            "job_id": job_id,
            "deployment_id": request.deployment_id,
            "model_alias": request.model_alias,
            "mode": request.mode.value,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "params": {
                "aspect_ratio": request.aspect_ratio,
                "style_preset": request.style_preset,
                "steps": request.steps,
                "guidance": request.guidance,
                "strength": request.strength,
                "seed": request.seed,
                "reference_artifact_ids": list(request.reference_artifact_ids),
            },
        }
        endpoint = self._deployment_endpoints.get(
            request.deployment_id, self._config.worker_base_url
        )
        async with self._client(endpoint) as client:
            response = await client.post("/v1/generation/jobs", json=payload)
            response.raise_for_status()
            data = response.json()
        self._requests[job_id] = request
        self._job_deployments[job_id] = request.deployment_id
        self._deployment_endpoints[request.deployment_id] = endpoint
        return self._normalize(data, request=request)

    async def get_job(self, job_id: str) -> GenerationJob:
        request = self._requests.get(job_id)
        endpoint = self._endpoint_for_job(job_id)
        async with self._client(endpoint) as client:
            response = await client.get(f"/v1/generation/jobs/{job_id}")
            if response.status_code == 404:
                raise KeyError(f"Unknown generation job: {job_id}")
            response.raise_for_status()
            data = response.json()
        if request is None:
            raw_mode = str(data.get("mode") or "txt2img")
            try:
                mode = GenerationMode(raw_mode)
            except ValueError:
                mode = GenerationMode.TEXT_TO_IMAGE
            request = GenerationRequest(
                request_id=job_id.removeprefix("diffusers-"),
                model_alias=str(data.get("model_alias") or "yonder-image"),
                deployment_id=str(
                    data.get("deployment_id")
                    or self._job_deployments.get(job_id)
                    or ""
                ),
                mode=mode,
                prompt=str(data.get("prompt") or ""),
            )
            self._requests[job_id] = request
            if request.deployment_id:
                self._job_deployments[job_id] = request.deployment_id
        return self._normalize(data, request=request)

    async def cancel_job(self, job_id: str) -> GenerationJob:
        request = self._requests.get(job_id)
        endpoint = self._endpoint_for_job(job_id)
        async with self._client(endpoint) as client:
            response = await client.post(f"/v1/generation/jobs/{job_id}/cancel")
            if response.status_code == 404:
                raise KeyError(f"Unknown generation job: {job_id}")
            response.raise_for_status()
            data = response.json()
        if request is None:
            raise KeyError(f"Unknown generation job: {job_id}")
        return self._normalize(data, request=request)

    async def fetch_artifact_bytes(
        self, *, deployment_id: str, artifact_id: str
    ) -> tuple[bytes, str, str]:
        """Pull a worker-local artifact into the Gateway process for storage."""
        endpoint = self._deployment_endpoints.get(
            deployment_id, self._config.worker_base_url
        )
        async with self._client(endpoint) as client:
            response = await client.get(f"/v1/generation/artifacts/{artifact_id}")
            response.raise_for_status()
            mime_type = response.headers.get(
                "content-type", "application/octet-stream"
            ).split(";")[0]
            filename = artifact_id
            disposition = response.headers.get("content-disposition") or ""
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip().strip('"')
            return response.content, mime_type, filename

    def _normalize(
        self, data: dict[str, Any], *, request: GenerationRequest
    ) -> GenerationJob:
        raw_assets = data.get("assets") or []
        assets: list[GenerationAssetRef] = []
        for item in raw_assets:
            raw_kind = str(item.get("kind") or "reference")
            try:
                kind = MediaKind(raw_kind)
            except ValueError:
                kind = MediaKind.REFERENCE
            artifact_id = str(item.get("artifact_id") or item.get("id") or "")
            if not artifact_id:
                continue
            location = (
                item.get("url")
                or item.get("path")
                or (
                    f"worker-inline://{artifact_id}"
                    if item.get("base64") or item.get("bytes")
                    else f"worker-artifact://{artifact_id}"
                )
            )
            assets.append(
                GenerationAssetRef(
                    artifact_id=artifact_id,
                    kind=kind,
                    mime_type=str(
                        item.get("mime_type") or "application/octet-stream"
                    ),
                    url=str(location),
                    width=item.get("width"),
                    height=item.get("height"),
                    duration_seconds=item.get("duration_seconds"),
                )
            )

        progress = tuple(
            GenerationProgress(
                job_id=str(item.get("job_id") or data.get("job_id") or ""),
                step=str(item.get("step") or "worker"),
                message=str(item.get("message") or ""),
                percent=item.get("percent"),
                details=dict(item.get("details") or {}),
            )
            for item in (data.get("progress") or [])
        )
        status_value = str(data.get("status") or "failed")
        try:
            status = GenerationJobStatus(status_value)
        except ValueError:
            status = GenerationJobStatus.FAILED
        metadata = {
            "worker_id": str(data.get("worker_id") or ""),
            "worker_artifacts": json.dumps(raw_assets, separators=(",", ":")),
        }
        return GenerationJob(
            job_id=str(data.get("job_id") or f"diffusers-{request.request_id}"),
            request_id=request.request_id,
            status=status,
            mode=request.mode,
            model_alias=request.model_alias,
            deployment_id=request.deployment_id,
            provider=self.name,
            availability=str(data.get("availability") or "not_reported"),
            progress=progress,
            assets=tuple(assets),
            error=str(data["error"]) if data.get("error") else None,
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            metadata=metadata,
        )


_: type[GenerationProvider] = DiffusersGenerationProvider
