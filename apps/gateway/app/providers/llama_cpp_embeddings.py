"""OpenAI-compatible embedding adapter for worker-managed llama.cpp endpoints."""

from __future__ import annotations

from typing import Any

import httpx
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.embeddings import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingVector,
)
from yonder_contracts.inference import (
    Deployment,
    DeploymentSpec,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderModel,
)


class LlamaCppEmbeddingProvider:
    """Per-deployment llama.cpp embedding runtime adapter."""

    name = "llama-cpp-embeddings"

    def __init__(
        self,
        *,
        request_timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = request_timeout_seconds
        self._transport = transport
        self._loaded: dict[str, str] = {}
        self._endpoints: dict[str, str] = {}

    def _client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=self._timeout,
            transport=self._transport,
            headers=worker_auth_headers(),
        )

    async def health(self) -> ProviderHealth:
        if not self._endpoints:
            return ProviderHealth(
                status=ProviderHealthStatus.UNHEALTHY,
                provider=self.name,
                message="No embedding deployments are loaded",
                details={"availability": "not_configured"},
            )
        for endpoint in self._endpoints.values():
            try:
                async with self._client(endpoint) as client:
                    response = await client.get("/health")
                    response.raise_for_status()
                return ProviderHealth(
                    status=ProviderHealthStatus.HEALTHY,
                    provider=self.name,
                    message="At least one llama.cpp embedding endpoint is reachable",
                    details={
                        "availability": "available",
                        "endpoints": list(self._endpoints.values()),
                    },
                )
            except httpx.HTTPError:
                continue
        return ProviderHealth(
            status=ProviderHealthStatus.UNHEALTHY,
            provider=self.name,
            message="llama.cpp embedding runtime is unavailable",
            details={"availability": "collection_failed"},
        )

    async def discover_models(self, *, base_url: str | None = None) -> list[ProviderModel]:
        if base_url is None:
            if not self._endpoints:
                return []
            base_url = next(iter(self._endpoints.values()))
        async with self._client(base_url) as client:
            response = await client.get("/v1/models")
            response.raise_for_status()
            payload = response.json()
        models: list[ProviderModel] = []
        for item in payload.get("data", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            models.append(
                ProviderModel(
                    id=model_id,
                    display_name=model_id,
                    owned_by=str(item.get("owned_by") or "llama.cpp"),
                    capabilities=["embeddings"],
                )
            )
        return models

    async def load(self, deployment: DeploymentSpec) -> Deployment:
        endpoint = (deployment.worker_endpoint or "").rstrip("/")
        if not endpoint:
            raise RuntimeError("llama.cpp embedding load requires worker_endpoint")
        expected = deployment.model_identifier.strip()
        discovered = await self.discover_models(base_url=endpoint)
        available = {model.id for model in discovered}
        if expected not in available:
            raise RuntimeError(
                "Configured embedding model is not being served "
                f"(expected={expected!r}, available={sorted(available)!r})"
            )
        self._loaded[deployment.deployment_id] = expected
        self._endpoints[deployment.deployment_id] = endpoint
        return Deployment(
            deployment_id=deployment.deployment_id,
            status="ready",
            provider=self.name,
            details={
                "model": expected,
                "base_url": endpoint,
                "profile": "embedding",
            },
        )

    async def unload(self, deployment_id: str) -> None:
        self._loaded.pop(deployment_id, None)
        self._endpoints.pop(deployment_id, None)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        model = self._loaded.get(request.deployment_id)
        endpoint = self._endpoints.get(request.deployment_id)
        if model is None or endpoint is None:
            raise RuntimeError(
                f"Embedding deployment is not loaded: {request.deployment_id}"
            )
        payload: dict[str, Any] = {
            "model": model,
            "input": list(request.inputs),
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        async with self._client(endpoint) as client:
            response = await client.post("/v1/embeddings", json=payload)
            response.raise_for_status()
            body = response.json()
        vectors: list[EmbeddingVector] = []
        for item in body.get("data", []):
            embedding = item.get("embedding") or []
            if not embedding:
                raise RuntimeError("Embedding response contained an empty vector")
            if all(float(value) == 0.0 for value in embedding):
                raise RuntimeError("Embedding response contained an all-zero vector")
            vectors.append(
                EmbeddingVector(
                    index=int(item.get("index", len(vectors))),
                    embedding=tuple(float(value) for value in embedding),
                )
            )
        if not vectors:
            raise RuntimeError("Embedding response contained no vectors")
        usage = body.get("usage")
        return EmbeddingResult(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            model=request.model_alias,
            data=tuple(vectors),
            provider=self.name,
            availability="available",
            usage=dict(usage) if isinstance(usage, dict) else None,
        )
