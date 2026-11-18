"""Embedding provider contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from yonder_contracts.inference import ProviderHealth, ProviderModel


@dataclass(frozen=True)
class EmbeddingRequest:
    request_id: str
    model_alias: str
    deployment_id: str
    inputs: tuple[str, ...]
    dimensions: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingVector:
    index: int
    embedding: tuple[float, ...]
    object: str = "embedding"


@dataclass(frozen=True)
class EmbeddingResult:
    request_id: str
    deployment_id: str
    model: str
    data: tuple[EmbeddingVector, ...]
    provider: str
    # Honest labeling — mock embeddings are deterministic, not a trained model.
    availability: str = "mock"
    usage: dict[str, int] | None = None


class EmbeddingProvider(Protocol):
    """Adapter boundary for embedding runtimes."""

    name: str

    async def health(self) -> ProviderHealth: ...

    async def discover_models(self) -> list[ProviderModel]: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...
