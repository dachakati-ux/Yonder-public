"""Reranking provider contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from yonder_contracts.inference import ProviderHealth, ProviderModel


@dataclass(frozen=True)
class RerankRequest:
    request_id: str
    model_alias: str
    deployment_id: str
    query: str
    documents: tuple[str, ...]
    top_n: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankResultItem:
    index: int
    document: str
    relevance_score: float


@dataclass(frozen=True)
class RerankResult:
    request_id: str
    deployment_id: str
    model: str
    results: tuple[RerankResultItem, ...]
    provider: str
    availability: str = "available"
    usage: dict[str, int] | None = None


class RerankingProvider(Protocol):
    """Adapter boundary for reranking runtimes."""

    name: str

    async def health(self) -> ProviderHealth: ...

    async def discover_models(self) -> list[ProviderModel]: ...

    async def rerank(self, request: RerankRequest) -> RerankResult: ...
