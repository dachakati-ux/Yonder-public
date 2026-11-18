"""Provider registry — routes never import concrete providers directly."""

from __future__ import annotations

from typing import Any


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._embedding_providers: dict[str, Any] = {}
        self._rerank_providers: dict[str, Any] = {}
        self._generation_providers: dict[str, Any] = {}

    def register(self, name: str, provider: Any) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> Any | None:
        return self._providers.get(name)

    def require(self, name: str) -> Any:
        provider = self.get(name)
        if provider is None:
            raise KeyError(f"Unknown inference provider: {name}")
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def register_embedding(self, name: str, provider: Any) -> None:
        self._embedding_providers[name] = provider

    def get_embedding(self, name: str) -> Any | None:
        return self._embedding_providers.get(name)

    def require_embedding(self, name: str) -> Any:
        provider = self.get_embedding(name)
        if provider is None:
            raise KeyError(f"Unknown embedding provider: {name}")
        return provider

    def embedding_names(self) -> list[str]:
        return sorted(self._embedding_providers)

    def register_rerank(self, name: str, provider: Any) -> None:
        self._rerank_providers[name] = provider

    def get_rerank(self, name: str) -> Any | None:
        return self._rerank_providers.get(name)

    def require_rerank(self, name: str) -> Any:
        provider = self.get_rerank(name)
        if provider is None:
            raise KeyError(f"Unknown rerank provider: {name}")
        return provider

    def rerank_names(self) -> list[str]:
        return sorted(self._rerank_providers)

    def register_generation(self, name: str, provider: Any) -> None:
        self._generation_providers[name] = provider

    def get_generation(self, name: str) -> Any | None:
        return self._generation_providers.get(name)

    def require_generation(self, name: str) -> Any:
        provider = self.get_generation(name)
        if provider is None:
            raise KeyError(f"Unknown generation provider: {name}")
        return provider

    def generation_names(self) -> list[str]:
        return sorted(self._generation_providers)

    def resolve(self, name: str) -> Any | None:
        return (
            self.get(name)
            or self.get_embedding(name)
            or self.get_rerank(name)
            or self.get_generation(name)
        )

    def require_any(self, name: str) -> Any:
        provider = self.resolve(name)
        if provider is None:
            raise KeyError(f"Unknown provider: {name}")
        return provider

    def all_names(self) -> set[str]:
        return set(self.names()) | set(self.embedding_names()) | set(
            self.rerank_names()
        ) | set(self.generation_names())
