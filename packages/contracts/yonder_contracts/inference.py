"""Inference provider contracts shared across gateway and workers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from yonder_contracts.structured import StructuredResponseFormat
from yonder_contracts.tools import ToolCall, ToolDefinition


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProviderHealthStatus
    provider: str
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str | None = None
    owned_by: str = "yonder"
    capabilities: list[str] = Field(default_factory=list)


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    model_definition_id: str
    model_identifier: str
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    gpu_assignments: list[int] = Field(default_factory=list)
    placement: dict[str, Any] | None = None
    worker_endpoint: str | None = None


class Deployment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    status: str
    provider: str
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None


@dataclass(frozen=True)
class ChatRequest:
    request_id: str
    model_alias: str
    deployment_id: str
    messages: tuple[ChatMessage, ...]
    stream: bool = True
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] | None = None
    tools: tuple[ToolDefinition, ...] | None = None
    tool_choice: str | None = None
    response_format: StructuredResponseFormat | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatUsage:
    """Authoritative tokenizer counts from a provider when available."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ChatChunk:
    """Normalized chat chunk — never expose provider-specific formats past adapters."""

    request_id: str
    deployment_id: str
    model: str
    delta_content: str | None = None
    finish_reason: str | None = None
    index: int = 0
    tool_calls: tuple[ToolCall, ...] | None = None
    structured_content: str | None = None
    usage: ChatUsage | None = None


class InferenceProvider(Protocol):
    """Adapter boundary for inference runtimes."""

    name: str

    async def health(self) -> ProviderHealth: ...

    async def discover_models(self) -> list[ProviderModel]: ...

    async def load(self, deployment: DeploymentSpec) -> Deployment: ...

    async def unload(self, deployment_id: str) -> None: ...

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...
