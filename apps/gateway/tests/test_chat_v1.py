"""Phase 2 OpenAI-compatible chat and routing tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.models.openai import ChatCompletionRequest
from app.providers.mock import MockStreamingProvider
from app.routing import DeploymentState, RouteState
from app.services.chat import ChatService
from fastapi.testclient import TestClient
from yonder_contracts.errors import ErrorCode
from yonder_contracts.inference import ChatChunk, ChatRequest
from yonder_contracts.reranking import RerankResult, RerankResultItem

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def test_list_models_includes_alias(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "yonder-chat" in ids


def test_unknown_model_returns_structured_404(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == ErrorCode.UNKNOWN_MODEL.value
    assert "request_id" in body["error"]
    assert response.headers.get("X-Yonder-Request-Id")


def test_non_streaming_chat_completion(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "yonder-chat"
    content = body["choices"][0]["message"]["content"]
    assert "mock response" in content.lower()
    assert "ping" in content
    assert body["usage"] is None


def test_streaming_chat_completion(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "stream please"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        lines = [line for line in response.iter_lines() if line]
        assert any(line == "data: [DONE]" for line in lines)
        data_lines = [
            line[6:]
            for line in lines
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert data_lines
        first = json.loads(data_lines[0])
        assert first["object"] == "yonder.context"
        assert "meter" in first
        chunk = next(
            json.loads(line)
            for line in data_lines
            if json.loads(line).get("object") == "chat.completion.chunk"
        )
        assert chunk["model"] == "yonder-chat"


def test_v1_module_does_not_import_mock_provider() -> None:
    import inspect

    import app.api.v1 as v1_module

    source = inspect.getsource(v1_module)
    assert "MockStreamingProvider" not in source
    assert "providers.mock" not in source


def test_disabled_route_returns_unavailable(client: TestClient) -> None:
    client.app.state.routes.aliases["yonder-chat"].state = RouteState.DISABLED
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.MODEL_UNAVAILABLE.value


def test_provider_failure_returns_structured_502(client: TestClient) -> None:
    class FailingProvider(MockStreamingProvider):
        async def chat(self, request: ChatRequest):
            raise RuntimeError("simulated provider crash")
            yield  # pragma: no cover — make this an async generator

    client.app.state.providers.register("mock", FailingProvider(chunk_delay_seconds=0.0))
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "boom"}],
            "stream": False,
        },
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == ErrorCode.PROVIDER_FAILURE.value


def test_missing_provider_does_not_leak_admission_slot(client: TestClient) -> None:
    deployment = client.app.state.routes.deployments["yonder-chat-mock-primary"]
    deployment.provider = "missing-provider"

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 502
    assert deployment.active_requests == 0
    assert deployment.queue_depth == 0


@pytest.mark.asyncio
async def test_streaming_provider_stall_times_out_and_releases_slot(
    client: TestClient,
) -> None:
    class StallingProvider(MockStreamingProvider):
        async def chat(self, request: ChatRequest):
            await asyncio.sleep(1)
            yield ChatChunk(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
            )

    client.app.state.providers.register("mock", StallingProvider())
    client.app.state.settings.admission.inference_timeout_seconds = 0.01
    service = client.app.state.chat_service
    service.admission.inference_timeout_seconds = 0.01
    body = ChatCompletionRequest(
        model="yonder-chat",
        messages=[{"role": "user", "content": "stall"}],
        stream=True,
    )

    with pytest.raises(Exception) as raised:
        async for _line in service.stream(request_id="req_stall", body=body):
            pass

    assert raised.value.code == ErrorCode.INFERENCE_TIMEOUT
    deployment = client.app.state.routes.deployments["yonder-chat-mock-primary"]
    assert deployment.active_requests == 0


def test_loading_deployment_returns_model_loading(client: TestClient) -> None:
    deployment = client.app.state.routes.deployments["yonder-chat-mock-primary"]
    deployment.status = DeploymentState.LOADING
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "yonder-chat",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.MODEL_LOADING.value


def test_resolve_and_chat_service_use_registry_not_hardcoded_provider() -> None:
    from app.config.bootstrap import build_provider_registry, build_route_registry
    from yonder_configuration import load_settings

    settings = load_settings(config_file=ROOT / "config/environments/development.yaml")
    routes = build_route_registry(settings)
    providers = build_provider_registry(settings)
    service = ChatService(routes=routes, providers=providers, admission=settings.admission)
    assert "mock" in providers.names()
    assert routes.get_alias("yonder-chat") is not None
    assert service.providers.get("mock") is not None


def test_embeddings_mock_lane_and_responses_still_501(client: TestClient) -> None:
    embeddings = client.post(
        "/v1/embeddings",
        json={"model": "yonder-embed", "input": ["hello", "world"], "dimensions": 16},
    )
    assert embeddings.status_code == 200
    payload = embeddings.json()
    assert payload["model"] == "yonder-embed"
    assert len(payload["data"]) == 2
    assert len(payload["data"][0]["embedding"]) == 16
    assert payload["yonder"]["availability"] == "mock"
    assert payload["yonder"]["provider"] == "mock-embeddings"

    unknown = client.post("/v1/embeddings", json={"model": "nope", "input": "hi"})
    assert unknown.status_code == 404

    responses = client.post("/v1/responses", json={"model": "x", "input": "hi"})
    assert responses.status_code == 501


def test_embeddings_alias_without_embedding_provider_fails_explicitly(
    client: TestClient,
) -> None:
    deployment = client.app.state.routes.deployments["yonder-embed-mock-primary"]
    deployment.provider = "mock"

    response = client.post(
        "/v1/embeddings",
        json={"model": "yonder-embed", "input": "no silent fallback"},
    )

    assert response.status_code == 501
    body = response.json()
    assert body["error"]["details"]["availability"] == "not_configured"
    assert body["error"]["details"]["provider"] == "mock"


def test_rerank_happy_path(client: TestClient) -> None:
    class StubReranker:
        async def rerank(self, request):
            return RerankResult(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
                results=(
                    RerankResultItem(
                        index=1,
                        document=request.documents[1],
                        relevance_score=0.91,
                    ),
                ),
                provider="test-reranker",
            )

    deployment = client.app.state.routes.deployments["yonder-embed-mock-primary"]
    deployment.provider = "test-reranker"
    client.app.state.providers.register_rerank("test-reranker", StubReranker())

    response = client.post(
        "/v1/rerank",
        json={
            "model": "yonder-embed",
            "query": "capital of France",
            "documents": ["London", "Paris"],
            "top_n": 1,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == [
        {"index": 1, "document": "Paris", "relevance_score": 0.91}
    ]
    assert body["yonder"]["provider"] == "test-reranker"
