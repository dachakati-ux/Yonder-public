"""Mock streaming inference provider for Phase 2 gateway foundation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from yonder_contracts.inference import (
    ChatChunk,
    ChatRequest,
    Deployment,
    DeploymentSpec,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderModel,
)
from yonder_contracts.tools import ToolCall


class MockStreamingProvider:
    """Deterministic in-process provider used until a real V100 runtime is wired.

    API routes must obtain this only through ProviderRegistry.
    """

    name = "mock"

    def __init__(self, *, chunk_delay_seconds: float = 0.01) -> None:
        self._chunk_delay_seconds = chunk_delay_seconds
        self._loaded: dict[str, DeploymentSpec] = {}

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            status=ProviderHealthStatus.HEALTHY,
            provider=self.name,
            message="Mock provider is healthy",
            details={"availability": "mock"},
        )

    async def discover_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="mock-chat",
                display_name="Mock Chat",
                owned_by="yonder-mock",
                capabilities=["chat", "streaming", "tools", "structured"],
            )
        ]

    async def load(self, deployment: DeploymentSpec) -> Deployment:
        self._loaded[deployment.deployment_id] = deployment
        return Deployment(
            deployment_id=deployment.deployment_id,
            status="ready",
            provider=self.name,
        )

    async def unload(self, deployment_id: str) -> None:
        self._loaded.pop(deployment_id, None)

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        last_user = ""
        for message in reversed(request.messages):
            if message.role == "user" and message.content:
                last_user = message.content
                break

        # Structured JSON path when response_format is requested.
        if request.response_format is not None:
            payload = {
                "summary": f"Mock structured answer for: {last_user[:120]}",
                "confidence": 0.42,
                "provider": "mock",
                "availability": "mock",
            }
            if (
                request.response_format.type == "json_schema"
                and request.response_format.json_schema
            ):
                payload["schema_name"] = request.response_format.name or "unnamed"
            text = json.dumps(payload, ensure_ascii=True)
            yield ChatChunk(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
                delta_content=text,
                structured_content=text,
                finish_reason="stop",
                index=0,
            )
            return

        # Tool-call path when tools are supplied and tool_choice is not none.
        if request.tools and request.tool_choice != "none":
            tool = request.tools[0]
            call = ToolCall(
                id=f"call_{request.request_id}",
                name=tool.name,
                arguments={"query": last_user[:200] or "ping"},
            )
            yield ChatChunk(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
                delta_content=None,
                tool_calls=(call,),
                finish_reason="tool_calls",
                index=0,
            )
            return

        reply = (
            f"Yonder mock response for alias '{request.model_alias}' "
            f"via deployment '{request.deployment_id}'. "
            f"You said: {last_user[:200]}"
        )
        words = reply.split(" ")
        for index, word in enumerate(words):
            piece = word if index == len(words) - 1 else f"{word} "
            yield ChatChunk(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                model=request.model_alias,
                delta_content=piece,
                index=0,
            )
            if self._chunk_delay_seconds > 0:
                await asyncio.sleep(self._chunk_delay_seconds)

        yield ChatChunk(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            model=request.model_alias,
            delta_content=None,
            finish_reason="stop",
            index=0,
        )
