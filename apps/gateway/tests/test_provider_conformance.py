"""Conformance suite for every configured provider adapter."""

from __future__ import annotations

import asyncio

import pytest
from app.providers.mock import MockStreamingProvider
from app.providers.mock_embeddings import MockEmbeddingProvider
from app.providers.mock_generation import MockGenerationProvider
from provider_conformance import (
    assert_embedding_provider_conformance,
    assert_generation_provider_conformance,
    assert_provider_conformance,
)


@pytest.mark.asyncio
async def test_mock_provider_conforms_to_adapter_contract() -> None:
    provider = MockStreamingProvider(chunk_delay_seconds=0)
    await assert_provider_conformance(provider)


@pytest.mark.asyncio
async def test_mock_embedding_provider_conforms() -> None:
    await assert_embedding_provider_conformance(MockEmbeddingProvider())


@pytest.mark.asyncio
async def test_mock_generation_provider_conforms() -> None:
    await assert_generation_provider_conformance(MockGenerationProvider())


@pytest.mark.asyncio
async def test_provider_chat_is_cancellable() -> None:
    provider = MockStreamingProvider(chunk_delay_seconds=1)

    async def consume() -> None:
        from yonder_contracts.inference import ChatRequest

        request = ChatRequest(
            request_id="req_cancel",
            model_alias="cancel-alias",
            deployment_id="cancel-deployment",
            messages=(),
        )
        async for _chunk in provider.chat(request):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
