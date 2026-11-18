"""OpenAI-compatible request and response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatToolFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChatTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    function: ChatToolFunction


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["json_object", "json_schema", "text"] = "text"
    json_schema: dict[str, Any] | None = None
    name: str | None = None
    strict: bool = True


class ChatMessageToolCallFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # JSON-encoded arguments string, per the OpenAI wire format.
    arguments: str = "{}"


class ChatMessageToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["function"] = "function"
    function: ChatMessageToolCallFunction


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    # Assistant messages may carry the tool calls they issued (required for
    # multi-turn tool use so the chat template can correlate tool results).
    tool_calls: list[ChatMessageToolCall] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    user: str | None = None
    n: int | None = Field(default=1, ge=1, le=1)
    tools: list[ChatTool] | None = None
    tool_choice: str | None = None
    response_format: ResponseFormat | None = None
    # Yonder extension: lets Gateway persist auto-condensed transcripts.
    conversation_id: str | None = None
    # Yonder extension: internal summary calls set this to avoid recursion.
    yonder_skip_condense: bool = False

    @field_validator("messages")
    @classmethod
    def require_content(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if not any(message.content for message in messages):
            raise ValueError("at least one message must include content")
        return messages


class ModelCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "yonder"


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["list"] = "list"
    data: list[ModelCard]


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    # Providers report usage only when they have authoritative tokenizer counts.
    # None is explicit: usage was not reported by the selected provider.
    usage: ChatCompletionUsage | None = None


class ChatCompletionChunkDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
    usage: ChatCompletionUsage | None = None


class YonderContextMeter(BaseModel):
    """Honest context pressure for Chat UI — never invents provider tokens."""

    model_config = ConfigDict(extra="forbid")

    context_length: int | None = None
    context_length_availability: Literal["available", "not_reported"] = "not_reported"
    prompt_chars: int = 0
    estimate_prompt_tokens: int | None = None
    pressure_ratio: float | None = None
    pressure_availability: Literal["estimate", "provider", "not_reported"] = (
        "not_reported"
    )
    prompt_tokens: int | None = None
    soft_ratio: float = 0.7
    should_condense: bool = False


class YonderContextEvent(BaseModel):
    """SSE control event emitted when Gateway prepares / condenses context."""

    model_config = ConfigDict(extra="forbid")

    object: Literal["yonder.context"] = "yonder.context"
    condensed: bool
    meter: YonderContextMeter
    messages: list[dict[str, str]] = Field(default_factory=list)


class EmbeddingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input: str | list[str]
    encoding_format: Literal["float", "base64"] | None = "float"
    dimensions: int | None = Field(default=None, ge=1, le=4096)
    user: str | None = None


class EmbeddingData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class EmbeddingsUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["list"] = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingsUsage | None = None
    # Honest availability for mock vs real providers
    yonder: dict[str, str] = Field(default_factory=dict)


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input: str | list[dict[str, Any]]
    stream: bool = False
