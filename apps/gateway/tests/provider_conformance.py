"""Reusable conformance checks for Yonder provider adapters."""

from __future__ import annotations

from typing import Any

from yonder_contracts.embeddings import EmbeddingRequest
from yonder_contracts.generation import GenerationMode, GenerationRequest
from yonder_contracts.inference import (
    ChatRequest,
    DeploymentSpec,
    ProviderHealth,
    ProviderModel,
)
from yonder_contracts.structured import StructuredResponseFormat
from yonder_contracts.tools import ToolDefinition


async def assert_provider_conformance(provider: Any) -> None:
    """Exercise the chat inference adapter contract without assuming a specific runtime."""

    health = await provider.health()
    assert isinstance(health, ProviderHealth)
    assert health.provider == provider.name

    models = await provider.discover_models()
    assert all(isinstance(model, ProviderModel) for model in models)

    spec = DeploymentSpec(
        deployment_id="conformance-deployment",
        model_definition_id="conformance-model",
        model_identifier="conformance-identifier",
        runtime_configuration={"source": "provider-conformance-test"},
    )
    deployment = await provider.load(spec)
    assert deployment.deployment_id == spec.deployment_id
    assert deployment.provider == provider.name

    request = ChatRequest(
        request_id="req_provider_conformance",
        model_alias="conformance-alias",
        deployment_id=spec.deployment_id,
        messages=(),
        stream=True,
    )
    chunks = [chunk async for chunk in provider.chat(request)]
    assert chunks
    assert all(chunk.request_id == request.request_id for chunk in chunks)
    assert all(chunk.deployment_id == request.deployment_id for chunk in chunks)
    assert any(chunk.finish_reason is not None for chunk in chunks)

    # Optional structured / tools paths when the mock supports them.
    structured = ChatRequest(
        request_id="req_structured",
        model_alias="conformance-alias",
        deployment_id=spec.deployment_id,
        messages=(),
        response_format=StructuredResponseFormat(type="json_object"),
    )
    structured_chunks = [chunk async for chunk in provider.chat(structured)]
    assert structured_chunks
    assert any(chunk.structured_content or chunk.delta_content for chunk in structured_chunks)

    tools_req = ChatRequest(
        request_id="req_tools",
        model_alias="conformance-alias",
        deployment_id=spec.deployment_id,
        messages=(),
        tools=(
            ToolDefinition(
                name="echo",
                description="Echo tool",
                parameters_schema={"type": "object"},
            ),
        ),
    )
    tool_chunks = [chunk async for chunk in provider.chat(tools_req)]
    assert tool_chunks
    assert any(chunk.tool_calls for chunk in tool_chunks)

    await provider.unload(spec.deployment_id)


async def assert_embedding_provider_conformance(provider: Any) -> None:
    health = await provider.health()
    assert isinstance(health, ProviderHealth)
    assert health.provider == provider.name
    models = await provider.discover_models()
    assert models
    result = await provider.embed(
        EmbeddingRequest(
            request_id="req_embed",
            model_alias="yonder-embed",
            deployment_id="embed-dep",
            inputs=("hello", "world"),
            dimensions=16,
        )
    )
    assert result.provider == provider.name
    assert result.availability in {"mock", "ready", "not_supported"}
    assert len(result.data) == 2
    assert all(len(item.embedding) == 16 for item in result.data)


async def assert_generation_provider_conformance(provider: Any) -> None:
    health = await provider.health()
    assert isinstance(health, ProviderHealth)
    job = await provider.create_job(
        GenerationRequest(
            request_id="req_gen",
            model_alias="yonder-image",
            deployment_id="gen-dep",
            mode=GenerationMode.TEXT_TO_IMAGE,
            prompt="a cyan hex skyline",
        )
    )
    assert job.provider == provider.name
    fetched = await provider.get_job(job.job_id)
    assert fetched.job_id == job.job_id
    cancelled = await provider.cancel_job(job.job_id)
    assert cancelled.job_id == job.job_id
