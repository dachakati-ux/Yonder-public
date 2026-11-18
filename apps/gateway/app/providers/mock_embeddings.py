"""Mock embedding provider — deterministic vectors, labeled as mock."""

from __future__ import annotations

import hashlib
import struct

from yonder_contracts.embeddings import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingVector,
)
from yonder_contracts.inference import ProviderHealth, ProviderHealthStatus, ProviderModel


class MockEmbeddingProvider:
    """Deterministic fake embeddings for platform wiring until a real model is selected."""

    name = "mock-embeddings"
    default_dimensions = 32

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            status=ProviderHealthStatus.HEALTHY,
            provider=self.name,
            message="Mock embedding provider (deterministic vectors; not a trained model)",
            details={"availability": "mock"},
        )

    async def discover_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="mock-embed",
                display_name="Mock Embeddings",
                owned_by="yonder-mock",
                capabilities=["embeddings"],
            )
        ]

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        dims = request.dimensions or self.default_dimensions
        vectors: list[EmbeddingVector] = []
        for index, text in enumerate(request.inputs):
            vectors.append(
                EmbeddingVector(
                    index=index,
                    embedding=_deterministic_vector(text, dims),
                )
            )
        return EmbeddingResult(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            model=request.model_alias,
            data=tuple(vectors),
            provider=self.name,
            availability="mock",
            usage={
                "prompt_tokens": sum(max(1, len(t.split())) for t in request.inputs),
                "total_tokens": sum(max(1, len(t.split())) for t in request.inputs),
            },
        )


def _deterministic_vector(text: str, dimensions: int) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    seed = digest
    while len(values) < dimensions:
        for i in range(0, len(seed) - 3, 4):
            raw = struct.unpack_from(">I", seed, i)[0]
            values.append((raw / 0xFFFFFFFF) * 2.0 - 1.0)
            if len(values) >= dimensions:
                break
        seed = hashlib.sha256(seed).digest()
    # L2 normalize
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return tuple(v / norm for v in values)
