from __future__ import annotations

import json

import httpx
import pytest
from app.providers.llama_cpp import LlamaCppProvider
from yonder_configuration.models import LlamaCppProviderConfig
from yonder_contracts.inference import (
    ChatMessage,
    ChatRequest,
    DeploymentSpec,
    ProviderHealthStatus,
)
from yonder_contracts.structured import StructuredResponseFormat
from yonder_contracts.tools import ToolDefinition


def _provider(handler) -> LlamaCppProvider:
    return LlamaCppProvider(
        LlamaCppProviderConfig(
            enabled=True,
            base_url="http://runtime.test",
            served_model="qwen-test",
        ),
        transport=httpx.MockTransport(handler),
    )


def _model_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [{"id": "qwen-test", "owned_by": "llama.cpp"}],
        },
    )


@pytest.mark.asyncio
async def test_health_and_discovery_report_runtime_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return _model_response()
        raise AssertionError(request.url.path)

    provider = _provider(handler)
    health = await provider.health()
    models = await provider.discover_models()

    assert health.status == ProviderHealthStatus.HEALTHY
    assert [model.id for model in models] == ["qwen-test"]


@pytest.mark.asyncio
async def test_health_failure_is_explicit_not_fabricated() -> None:
    provider = _provider(lambda _request: httpx.Response(503))

    health = await provider.health()

    assert health.status == ProviderHealthStatus.UNHEALTHY
    assert health.details["availability"] == "collection_failed"


@pytest.mark.asyncio
async def test_load_fails_when_runtime_serves_a_different_model() -> None:
    provider = LlamaCppProvider(
        LlamaCppProviderConfig(
            enabled=True,
            base_url="http://runtime.test",
            served_model="expected-model",
        ),
        transport=httpx.MockTransport(lambda _request: _model_response()),
    )

    with pytest.raises(RuntimeError, match="not being served"):
        await provider.load(
            DeploymentSpec(
                deployment_id="real-primary",
                model_definition_id="real",
                model_identifier="expected-model",
            )
        )


@pytest.mark.asyncio
async def test_chat_normalizes_stream_and_forwards_constraints() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _model_response()
        if request.url.path == "/v1/chat/completions":
            captured.update(json.loads(request.content))
            body = (
                'data: {"choices":[{"index":0,"delta":{"content":"hello "},'
                '"finish_reason":null}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{"content":"world"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        raise AssertionError(request.url.path)

    provider = _provider(handler)
    await provider.load(
        DeploymentSpec(
            deployment_id="real-primary",
            model_definition_id="real",
            model_identifier="qwen-test",
        )
    )
    request = ChatRequest(
        request_id="req-real",
        model_alias="yonder-chat",
        deployment_id="real-primary",
        messages=(ChatMessage(role="user", content="hi"),),
        temperature=0.4,
        max_tokens=32,
        tools=(
            ToolDefinition(
                name="lookup",
                description="Look something up",
                parameters_schema={"type": "object"},
            ),
        ),
        response_format=StructuredResponseFormat(type="json_object"),
    )

    chunks = [chunk async for chunk in provider.chat(request)]

    assert "".join(chunk.delta_content or "" for chunk in chunks) == "hello world"
    assert chunks[-1].finish_reason == "stop"
    assert captured["model"] == "qwen-test"
    assert captured["tools"][0]["function"]["name"] == "lookup"
    assert captured["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_chat_forwards_provider_usage_on_final_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return _model_response()
        if request.url.path == "/v1/chat/completions":
            body = (
                'data: {"choices":[{"index":0,"delta":{"content":"hi"},'
                '"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":11,"completion_tokens":2,"total_tokens":13}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        raise AssertionError(request.url.path)

    provider = _provider(handler)
    await provider.load(
        DeploymentSpec(
            deployment_id="real-primary",
            model_definition_id="real",
            model_identifier="qwen-test",
        )
    )
    request = ChatRequest(
        request_id="req-usage",
        model_alias="yonder-chat",
        deployment_id="real-primary",
        messages=(ChatMessage(role="user", content="hi"),),
    )
    chunks = [chunk async for chunk in provider.chat(request)]
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens == 11
    assert chunks[-1].usage.total_tokens == 13


@pytest.mark.asyncio
async def test_load_maps_per_deployment_worker_endpoint() -> None:
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        if request.url.path == "/v1/models":
            return _model_response()
        if request.url.path == "/v1/chat/completions":
            body = (
                'data: {"choices":[{"index":0,"delta":{"content":"ok"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        raise AssertionError(request.url.path)

    provider = LlamaCppProvider(
        LlamaCppProviderConfig(
            enabled=True,
            base_url="http://static.test",
            served_model="qwen-test",
        ),
        transport=httpx.MockTransport(handler),
    )
    loaded = await provider.load(
        DeploymentSpec(
            deployment_id="placed-a",
            model_definition_id="real",
            model_identifier="qwen-test",
            worker_endpoint="http://worker-runtime.test:8100",
            gpu_assignments=[1],
        )
    )
    assert loaded.details["base_url"] == "http://worker-runtime.test:8100"
    assert loaded.details["runtime_lifecycle"] == "worker_managed"

    chunks = [
        chunk
        async for chunk in provider.chat(
            ChatRequest(
                request_id="req-placed",
                model_alias="yonder-chat",
                deployment_id="placed-a",
                messages=(ChatMessage(role="user", content="hi"),),
            )
        )
    ]
    assert "".join(chunk.delta_content or "" for chunk in chunks) == "ok"
    assert "worker-runtime.test" in seen_hosts

    await provider.unload("placed-a")
    with pytest.raises(RuntimeError, match="not loaded"):
        _ = [chunk async for chunk in provider.chat(
            ChatRequest(
                request_id="req-gone",
                model_alias="yonder-chat",
                deployment_id="placed-a",
                messages=(ChatMessage(role="user", content="hi"),),
            )
        )]


@pytest.mark.asyncio
async def test_worker_managed_load_ignores_static_served_model() -> None:
    """A worker-managed deployment is gated by its own manifest identifier.

    config.served_model describes only the static operator-managed endpoint, so a
    second model placed by the worker must not be rejected for failing to match it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "nemotron-3-nano-4b", "owned_by": "llama.cpp"}],
                },
            )
        raise AssertionError(request.url.path)

    provider = LlamaCppProvider(
        LlamaCppProviderConfig(
            enabled=True,
            base_url="http://static.test",
            served_model="qwen3.6-35b-a3b-q4",
        ),
        transport=httpx.MockTransport(handler),
    )

    loaded = await provider.load(
        DeploymentSpec(
            deployment_id="nemotron-wai1-g1",
            model_definition_id="yonder-nemotron-3-nano-4b",
            model_identifier="nemotron-3-nano-4b",
            worker_endpoint="http://127.0.0.1:8100",
            gpu_assignments=[1],
        )
    )

    assert loaded.status == "ready"
    assert loaded.details["model"] == "nemotron-3-nano-4b"
    assert loaded.details["runtime_lifecycle"] == "worker_managed"


@pytest.mark.asyncio
async def test_static_lane_still_enforces_configured_served_model() -> None:
    """Without a worker endpoint the static served_model remains authoritative."""
    provider = LlamaCppProvider(
        LlamaCppProviderConfig(
            enabled=True,
            base_url="http://static.test",
            served_model="expected-model",
        ),
        transport=httpx.MockTransport(lambda _request: _model_response()),
    )

    with pytest.raises(RuntimeError, match="not being served"):
        await provider.load(
            DeploymentSpec(
                deployment_id="static-a",
                model_definition_id="real",
                model_identifier="qwen-test",
            )
        )
