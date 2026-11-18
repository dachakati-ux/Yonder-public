"""llama.cpp /v1/rerank adapter for worker-managed endpoints."""

from __future__ import annotations

from typing import Any

import httpx
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.inference import (
    Deployment,
    DeploymentSpec,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderModel,
)
from yonder_contracts.reranking import RerankRequest, RerankResult, RerankResultItem


class LlamaCppRerankProvider:
    """Per-deployment llama.cpp rerank runtime adapter."""

    name = "llama-cpp-rerank"

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
                message="No rerank deployments are loaded",
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
                    message="At least one llama.cpp rerank endpoint is reachable",
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
            message="llama.cpp rerank runtime is unavailable",
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
                    capabilities=["reranking"],
                )
            )
        return models

    async def load(self, deployment: DeploymentSpec) -> Deployment:
        endpoint = (deployment.worker_endpoint or "").rstrip("/")
        if not endpoint:
            raise RuntimeError("llama.cpp rerank load requires worker_endpoint")
        expected = deployment.model_identifier.strip()
        discovered = await self.discover_models(base_url=endpoint)
        available = {model.id for model in discovered}
        if expected not in available:
            raise RuntimeError(
                "Configured rerank model is not being served "
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
                "profile": "rerank",
            },
        )

    async def unload(self, deployment_id: str) -> None:
        self._loaded.pop(deployment_id, None)
        self._endpoints.pop(deployment_id, None)

    async def rerank(self, request: RerankRequest) -> RerankResult:
        model = self._loaded.get(request.deployment_id)
        endpoint = self._endpoints.get(request.deployment_id)
        if model is None or endpoint is None:
            raise RuntimeError(
                f"Rerank deployment is not loaded: {request.deployment_id}"
            )
        if not request.query.strip():
            raise RuntimeError("rerank query must not be empty")
        if not request.documents:
            raise RuntimeError("rerank documents must not be empty")
        payload: dict[str, Any] = {
            "model": model,
            "query": request.query,
            "documents": list(request.documents),
        }
        if request.top_n is not None:
            payload["top_n"] = request.top_n
        async with self._client(endpoint) as client:
            response = await client.post("/v1/rerank", json=payload)
            response.raise_for_status()
            body = response.json()
        results_raw = body.get("results") or body.get("data") or []
        items: list[RerankResultItem] = []
        for item in results_raw:
            score = item.get("relevance_score", item.get("score"))
            if score is None:
                raise RuntimeError("Rerank response missing relevance score")
            index = int(item.get("index", len(items)))
            document = str(
                item.get("document")
                or (
                    request.documents[index]
                    if 0 <= index < len(request.documents)
                    else ""
                )
            )
            items.append(
                RerankResultItem(
                    index=index,
                    document=document,
                    relevance_score=float(score),
                )
            )
        if not items:
            raise RuntimeError("Rerank response contained no results")
        # Fail closed on the known near-zero broken-GGUF failure mode.
        if all(abs(item.relevance_score) < 1e-12 for item in items):
            raise RuntimeError(
                "Rerank scores are near-zero; GGUF likely missing classifier tensors"
            )
        usage = body.get("usage")
        return RerankResult(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            model=request.model_alias,
            results=tuple(items),
            provider=self.name,
            availability="available",
            usage=dict(usage) if isinstance(usage, dict) else None,
        )
