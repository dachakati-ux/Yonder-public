from __future__ import annotations

import json

import httpx
import pytest
from app.providers.llama_cpp_embeddings import LlamaCppEmbeddingProvider
from app.providers.llama_cpp_rerank import LlamaCppRerankProvider
from yonder_contracts.embeddings import EmbeddingRequest
from yonder_contracts.inference import DeploymentSpec
from yonder_contracts.reranking import RerankRequest


def _models() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": [{"id": "runtime-model", "owned_by": "llama.cpp"}]},
    )


def test_bootstrap_registers_real_embedding_and_rerank_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    from app.config.bootstrap import build_provider_registry
    from yonder_configuration import load_settings

    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("YONDER_LLAMA_CPP__ENABLED", "true")
    settings = load_settings(config_file=root / "config/environments/development.yaml")
    registry = build_provider_registry(settings)

    assert registry.get_embedding("llama-cpp-embeddings") is not None
    assert registry.get_rerank("llama-cpp-rerank") is not None
    assert "diffusers" not in registry.generation_names()


@pytest.mark.asyncio
async def test_embedding_provider_loads_and_forwards_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _models()
        if request.url.path == "/v1/embeddings":
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [0.25, -0.5]},
                        {"index": 1, "embedding": [0.75, 0.1]},
                    ],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                },
            )
        raise AssertionError(request.url.path)

    provider = LlamaCppEmbeddingProvider(transport=httpx.MockTransport(handler))
    await provider.load(
        DeploymentSpec(
            deployment_id="embed-a",
            model_definition_id="embed",
            model_identifier="runtime-model",
            worker_endpoint="http://worker.test:8110",
        )
    )
    result = await provider.embed(
        EmbeddingRequest(
            request_id="req-e",
            model_alias="yonder-embed",
            deployment_id="embed-a",
            inputs=("alpha", "beta"),
            dimensions=2,
        )
    )

    assert captured == {
        "model": "runtime-model",
        "input": ["alpha", "beta"],
        "dimensions": 2,
    }
    assert result.data[0].embedding == (0.25, -0.5)
    assert result.availability == "available"


@pytest.mark.asyncio
async def test_embedding_provider_rejects_all_zero_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _models()
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0, 0]}]})

    provider = LlamaCppEmbeddingProvider(transport=httpx.MockTransport(handler))
    await provider.load(
        DeploymentSpec(
            deployment_id="embed-a",
            model_definition_id="embed",
            model_identifier="runtime-model",
            worker_endpoint="http://worker.test",
        )
    )
    with pytest.raises(RuntimeError, match="all-zero"):
        await provider.embed(
            EmbeddingRequest(
                request_id="req-e",
                model_alias="embed",
                deployment_id="embed-a",
                inputs=("alpha",),
            )
        )


@pytest.mark.asyncio
async def test_rerank_provider_loads_and_normalizes_results() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _models()
        if request.url.path == "/v1/rerank":
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.2},
                        {"index": 0, "relevance_score": 0.9},
                    ]
                },
            )
        raise AssertionError(request.url.path)

    provider = LlamaCppRerankProvider(transport=httpx.MockTransport(handler))
    await provider.load(
        DeploymentSpec(
            deployment_id="rerank-a",
            model_definition_id="rerank",
            model_identifier="runtime-model",
            worker_endpoint="http://worker.test:8120",
        )
    )
    result = await provider.rerank(
        RerankRequest(
            request_id="req-r",
            model_alias="yonder-rerank",
            deployment_id="rerank-a",
            query="best",
            documents=("first", "second"),
            top_n=2,
        )
    )

    assert captured["model"] == "runtime-model"
    assert captured["top_n"] == 2
    assert result.results[0].document == "second"
    assert result.results[1].relevance_score == 0.9


@pytest.mark.asyncio
async def test_rerank_provider_rejects_near_zero_scores() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _models()
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.0}]}
        )

    provider = LlamaCppRerankProvider(transport=httpx.MockTransport(handler))
    await provider.load(
        DeploymentSpec(
            deployment_id="rerank-a",
            model_definition_id="rerank",
            model_identifier="runtime-model",
            worker_endpoint="http://worker.test",
        )
    )
    with pytest.raises(RuntimeError, match="near-zero"):
        await provider.rerank(
            RerankRequest(
                request_id="req-r",
                model_alias="rerank",
                deployment_id="rerank-a",
                query="query",
                documents=("doc",),
            )
        )
