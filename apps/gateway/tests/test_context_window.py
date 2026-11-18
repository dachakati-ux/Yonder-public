"""Context window pressure + auto-condense tests."""

from __future__ import annotations

import pytest
from app.models.openai import ChatCompletionRequest
from app.models.openai import ChatMessage as ApiChatMessage
from app.providers.mock import MockStreamingProvider
from app.routing import DeploymentState, RouteRegistry, RouteState
from app.routing.models import DeploymentRecord, ModelAliasRoute
from app.services.chat import ChatService
from app.services.context_window import (
    ContextWindowService,
    measure_pressure,
    split_for_condensation,
)
from app.services.memory_store import InMemoryConversationStore
from yonder_configuration.models import AdmissionConfig, ContextWindowConfig
from yonder_contracts.inference import ChatChunk, ChatRequest, ChatUsage


def _deployment(*, context_length: int | None = 65536) -> DeploymentRecord:
    runtime: dict = {"lane": "test"}
    if context_length is not None:
        runtime["context_length"] = context_length
    return DeploymentRecord(
        id="dep-test",
        model_definition_id="model-test",
        worker_id="worker-test",
        provider="mock",
        status=DeploymentState.READY,
        gpu_assignments=[0],
        runtime_configuration=runtime,
        load_progress_percent=100,
    )


def test_measure_pressure_estimate_triggers_at_soft_ratio() -> None:
    config = ContextWindowConfig(
        enabled=True,
        soft_ratio=0.70,
        chars_per_token_estimate=4,
    )
    # 0.70 * 1000 = 700 tokens; reserve 100 → need estimate >= 600
    messages = [ApiChatMessage(role="user", content="x" * (600 * 4))]
    meter = measure_pressure(
        messages=messages,
        deployment=_deployment(context_length=1000),
        config=config,
        max_tokens=100,
        admission_output_tokens=4096,
    )
    assert meter.pressure_availability == "estimate"
    assert meter.should_condense is True
    assert meter.estimate_prompt_tokens == 600


def test_measure_pressure_not_reported_without_context_length() -> None:
    meter = measure_pressure(
        messages=[ApiChatMessage(role="user", content="hello")],
        deployment=_deployment(context_length=None),
        config=ContextWindowConfig(enabled=True),
        max_tokens=64,
        admission_output_tokens=4096,
    )
    assert meter.context_length_availability == "not_reported"
    assert meter.should_condense is False


def test_split_for_condensation_keeps_leading_system_and_recent_tail() -> None:
    messages = [
        ApiChatMessage(role="system", content="recipe"),
        ApiChatMessage(role="user", content="u1"),
        ApiChatMessage(role="assistant", content="a1"),
        ApiChatMessage(role="user", content="u2"),
        ApiChatMessage(role="assistant", content="a2"),
        ApiChatMessage(role="user", content="u3"),
        ApiChatMessage(role="assistant", content="a3"),
    ]
    split = split_for_condensation(messages, keep_recent_messages=2)
    assert split is not None
    leading, middle, recent = split
    assert [m.content for m in leading] == ["recipe"]
    assert [m.content for m in recent] == ["u3", "a3"]
    assert [m.content for m in middle] == ["u1", "a1", "u2", "a2"]


@pytest.mark.asyncio
async def test_replace_messages_persists_condensed_transcript() -> None:
    store = InMemoryConversationStore()
    convo = await store.create_conversation(
        principal_subject="op",
        title="t",
        model_alias="yonder-chat",
    )
    await store.append_message(convo.id, "op", role="user", content="old1")
    await store.append_message(convo.id, "op", role="assistant", content="old2")
    replaced = await store.replace_messages(
        convo.id,
        "op",
        messages=[
            {"role": "system", "content": "Yonder context summary (auto): kept"},
            {"role": "user", "content": "new"},
        ],
    )
    assert replaced is not None
    assert len(replaced) == 2
    loaded = await store.get_conversation(convo.id, "op")
    assert loaded is not None
    assert [m.content for m in loaded.messages] == [
        "Yonder context summary (auto): kept",
        "new",
    ]


@pytest.mark.asyncio
async def test_context_window_service_skips_when_flag_set() -> None:
    service = ContextWindowService(
        config=ContextWindowConfig(enabled=True, soft_ratio=0.01),
    )
    huge = [ApiChatMessage(role="user", content="x" * 50_000)]

    async def summarize(_middle: list[ApiChatMessage]) -> str:
        raise AssertionError("summarize must not run when skip_condense is set")

    result = await service.prepare(
        messages=huge,
        deployment=_deployment(),
        max_tokens=64,
        skip_condense=True,
        conversation_id=None,
        principal_subject=None,
        summarize=summarize,
    )
    assert result.condensed is False
    assert result.messages == huge


@pytest.mark.asyncio
async def test_chat_service_auto_condenses_and_persists() -> None:
    store = InMemoryConversationStore()
    convo = await store.create_conversation(
        principal_subject="op",
        title="long",
        model_alias="yonder-chat",
    )
    routes = RouteRegistry()
    deployment = _deployment(context_length=200)
    routes.upsert_deployment(deployment)
    routes.upsert_alias(
        ModelAliasRoute(
            alias="yonder-chat",
            deployment_id=deployment.id,
            state=RouteState.ENABLED,
            priority=100,
            experimental=False,
        )
    )

    class SummarizingProvider(MockStreamingProvider):
        async def chat(self, request: ChatRequest):
            text = "SUMMARY_OK"
            if any("Conversation turns to summarize" in m.content for m in request.messages):
                text = "prior turns summarized"
            for token in text.split(" "):
                yield ChatChunk(
                    request_id=request.request_id,
                    deployment_id=request.deployment_id,
                    model=request.model_alias,
                    delta_content=token + " ",
                )
            yield ChatChunk(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
                finish_reason="stop",
                usage=ChatUsage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            )

    class Providers:
        def require(self, name: str):
            assert name == "mock"
            return SummarizingProvider(chunk_delay_seconds=0.0)

    service = ChatService(
        routes=routes,
        providers=Providers(),
        admission=AdmissionConfig(max_prompt_chars=1_000_000),
        context_window=ContextWindowConfig(
            enabled=True,
            soft_ratio=0.70,
            chars_per_token_estimate=4,
            keep_recent_messages=2,
            summary_max_tokens=64,
        ),
        conversations=store,
    )

    # Enough chars to cross soft ratio on ctx=200 with reserve 64.
    history = [
        ApiChatMessage(role="user", content="u1-" + ("a" * 200)),
        ApiChatMessage(role="assistant", content="a1-" + ("b" * 200)),
        ApiChatMessage(role="user", content="u2-" + ("c" * 200)),
        ApiChatMessage(role="assistant", content="a2-" + ("d" * 200)),
        ApiChatMessage(role="user", content="latest question"),
    ]
    body = ChatCompletionRequest(
        model="yonder-chat",
        messages=history,
        stream=False,
        max_tokens=64,
        conversation_id=convo.id,
    )
    response = await service.complete(
        request_id="req_condense",
        body=body,
        principal_subject="op",
    )
    assert "SUMMARY_OK" in response.choices[0].message.content or response.usage is not None

    loaded = await store.get_conversation(convo.id, "op")
    assert loaded is not None
    assert any("Yonder context summary (auto):" in m.content for m in loaded.messages)
    assert loaded.messages[-1].content == "latest question"
    assert response.usage is not None
    assert response.usage.prompt_tokens == 10
