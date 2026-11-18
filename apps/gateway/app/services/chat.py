"""Chat completion orchestration through routing + provider adapters."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from yonder_configuration.models import AdmissionConfig, ContextWindowConfig
from yonder_contracts.errors import ErrorCode
from yonder_contracts.inference import ChatChunk, ChatMessage, ChatRequest
from yonder_contracts.structured import StructuredResponseFormat
from yonder_contracts.tools import ToolCall, ToolDefinition

from app.models.openai import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
)
from app.models.openai import (
    ChatMessage as ApiChatMessage,
)
from app.routing import (
    RouteRegistry,
    admit_request,
    estimate_prompt_chars,
    resolve_route,
)
from app.routing.gate import DeploymentAdmissionGate, GateRejection
from app.routing.resolve import RouteResolutionError
from app.services.context_window import (
    ContextPrepareResult,
    ContextWindowService,
    meter_with_provider_usage,
)
from app.services.conversations import ConversationStore


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """Decode the OpenAI JSON-string tool arguments; malformed input is {}."""
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


class ChatServiceError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ChatService:
    def __init__(
        self,
        *,
        routes: RouteRegistry,
        providers: Any,
        admission: AdmissionConfig,
        gate: DeploymentAdmissionGate | None = None,
        accepting_requests: Any | None = None,
        on_state_changed: Any | None = None,
        routing_policy: Any | None = None,
        context_window: ContextWindowConfig | None = None,
        conversations: ConversationStore | None = None,
        ensure_ready: Any | None = None,
    ) -> None:
        self.routes = routes
        self.providers = providers
        self.admission = admission
        self.gate = gate or DeploymentAdmissionGate(admission)
        self._accepting_requests = accepting_requests or (lambda: True)
        self._on_state_changed = on_state_changed
        self.routing_policy = routing_policy
        self.ensure_ready = ensure_ready
        self.context_window = ContextWindowService(
            config=context_window or ContextWindowConfig(),
            store=conversations,
            admission_output_tokens=admission.max_output_tokens,
        )

    def enforce_lane_access(self, model_alias: str, principal: Any) -> None:
        """Reject experimental aliases when the principal lacks the lane capability."""

        if self.routing_policy is None:
            return
        if not self.routing_policy.require_capability_for_experimental:
            return
        alias = self.routes.get_alias(model_alias)
        if alias is None or not alias.experimental:
            return
        capability = self.routing_policy.experimental_capability
        if principal is None or not principal.has_capability(capability):
            raise ChatServiceError(
                ErrorCode.PERMISSION_CONTEXT_REQUIRED,
                (
                    f"Alias '{model_alias}' is experimental and requires "
                    f"capability '{capability}'."
                ),
                details={
                    "model": model_alias,
                    "lane": "experimental",
                    "required_capability": capability,
                },
            )

    def _resolve(self, model: str):
        resolved = resolve_route(self.routes, model)
        if isinstance(resolved, RouteResolutionError):
            raise ChatServiceError(
                resolved.code,
                resolved.message,
                details=resolved.details,
            )
        return resolved

    def _admit_size(
        self, deployment, messages: list[ApiChatMessage], max_tokens: int | None
    ) -> None:
        decision = admit_request(
            deployment=deployment,
            admission=self.admission,
            prompt_chars=estimate_prompt_chars(messages),
            max_tokens=max_tokens,
        )
        if not decision.allowed:
            assert decision.code is not None
            raise ChatServiceError(
                decision.code,
                decision.message or "Request rejected by admission control",
                details=decision.details,
            )

    def _provider_for(self, provider_name: str):
        try:
            return self.providers.require(provider_name)
        except KeyError as exc:
            raise ChatServiceError(
                ErrorCode.PROVIDER_FAILURE,
                f"Inference provider is not configured: {provider_name}",
                details={"provider": provider_name},
            ) from exc

    def _to_chat_request(
        self,
        *,
        request_id: str,
        body: ChatCompletionRequest,
        deployment_id: str,
        messages: list[ApiChatMessage] | None = None,
    ) -> ChatRequest:
        stop: tuple[str, ...] | None
        if body.stop is None:
            stop = None
        elif isinstance(body.stop, str):
            stop = (body.stop,)
        else:
            stop = tuple(body.stop)

        source_messages = messages if messages is not None else body.messages
        return ChatRequest(
            request_id=request_id,
            model_alias=body.model,
            deployment_id=deployment_id,
            messages=tuple(
                ChatMessage(
                    role=message.role,
                    content=message.content or "",
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                    tool_calls=(
                        tuple(
                            ToolCall(
                                id=call.id,
                                name=call.function.name,
                                arguments=_parse_tool_arguments(
                                    call.function.arguments
                                ),
                            )
                            for call in message.tool_calls
                        )
                        if message.tool_calls
                        else None
                    ),
                )
                for message in source_messages
            ),
            stream=body.stream,
            temperature=body.temperature,
            top_p=body.top_p,
            max_tokens=body.max_tokens,
            stop=stop,
            tools=(
                tuple(
                    ToolDefinition(
                        name=tool.function.name,
                        description=tool.function.description or "",
                        parameters_schema=tool.function.parameters,
                    )
                    for tool in body.tools
                )
                if body.tools
                else None
            ),
            tool_choice=body.tool_choice,
            response_format=(
                StructuredResponseFormat(
                    type=body.response_format.type,
                    json_schema=body.response_format.json_schema,
                    name=body.response_format.name,
                    strict=body.response_format.strict,
                )
                if body.response_format
                else None
            ),
        )

    async def _collect_with_timeout(
        self, provider: Any, chat_request: ChatRequest
    ) -> list[ChatChunk]:
        async def _consume() -> list[ChatChunk]:
            chunks: list[ChatChunk] = []
            async for chunk in provider.chat(chat_request):
                chunks.append(chunk)
            return chunks

        try:
            return await asyncio.wait_for(
                _consume(),
                timeout=self.admission.inference_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ChatServiceError(
                ErrorCode.INFERENCE_TIMEOUT,
                "Inference exceeded the configured timeout.",
                details={
                    "inference_timeout_seconds": str(
                        self.admission.inference_timeout_seconds
                    )
                },
            ) from exc

    async def _summarize_middle(
        self,
        *,
        request_id: str,
        body: ChatCompletionRequest,
        deployment,
        middle: list[ApiChatMessage],
    ) -> str:
        provider = self._provider_for(deployment.provider)
        summary_messages = self.context_window.build_summary_prompt(middle)
        summary_body = body.model_copy(
            update={
                "messages": summary_messages,
                "stream": False,
                "max_tokens": self.context_window.config.summary_max_tokens,
                "yonder_skip_condense": True,
                "tools": None,
                "tool_choice": None,
            }
        )
        self._admit_size(deployment, summary_messages, summary_body.max_tokens)
        chat_request = self._to_chat_request(
            request_id=f"{request_id}_condense",
            body=summary_body,
            deployment_id=deployment.id,
            messages=summary_messages,
        )
        try:
            await self.gate.acquire(deployment)
        except GateRejection as exc:
            raise ChatServiceError(exc.code, exc.message, details=exc.details) from exc
        try:
            chunks = await self._collect_with_timeout(provider, chat_request)
        except ChatServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ChatServiceError(
                ErrorCode.PROVIDER_FAILURE,
                "Inference provider failed while condensing context.",
                details={"reason": str(exc), "provider": deployment.provider},
            ) from exc
        finally:
            await self.gate.release(deployment)
            if self._on_state_changed:
                self._on_state_changed()

        parts = [chunk.delta_content for chunk in chunks if chunk.delta_content]
        return "".join(parts)

    async def _prepare_context(
        self,
        *,
        request_id: str,
        body: ChatCompletionRequest,
        deployment,
        principal_subject: str | None,
    ) -> ContextPrepareResult:
        async def summarize(middle: list[ApiChatMessage]) -> str:
            return await self._summarize_middle(
                request_id=request_id,
                body=body,
                deployment=deployment,
                middle=middle,
            )

        return await self.context_window.prepare(
            messages=list(body.messages),
            deployment=deployment,
            max_tokens=body.max_tokens,
            skip_condense=body.yonder_skip_condense,
            conversation_id=body.conversation_id,
            principal_subject=principal_subject,
            summarize=summarize,
        )

    @staticmethod
    def _usage_from_chunks(chunks: list[ChatChunk]) -> ChatCompletionUsage | None:
        for chunk in reversed(chunks):
            if chunk.usage is not None:
                return ChatCompletionUsage(
                    prompt_tokens=chunk.usage.prompt_tokens,
                    completion_tokens=chunk.usage.completion_tokens,
                    total_tokens=chunk.usage.total_tokens,
                )
        return None

    async def _resolve_ready(
        self,
        model: str,
        *,
        request_id: str,
        principal_subject: str | None,
    ):
        try:
            return self._resolve(model)
        except ChatServiceError as exc:
            if self.ensure_ready is None:
                raise
            if exc.code not in {
                ErrorCode.MODEL_UNAVAILABLE,
                ErrorCode.MODEL_LOADING,
                ErrorCode.UNKNOWN_MODEL,
            }:
                raise
            from app.services.ensure_ready import EnsureReadyError

            try:
                await self.ensure_ready.ensure(
                    model_alias=model,
                    for_chat=True,
                    principal_subject=principal_subject or "anonymous",
                    request_id=request_id,
                )
            except EnsureReadyError as ensure_exc:
                # Ensure-ready is best-effort: its internal failures must not
                # rewrite the API contract (unknown model stays 404, unloaded
                # stays 503, loading stays MODEL_LOADING). Surface the ensure
                # failure in details for operators.
                details = dict(exc.details or {})
                details["ensure_ready"] = {
                    "code": ensure_exc.code.value,
                    "message": ensure_exc.message,
                }
                raise ChatServiceError(
                    exc.code,
                    exc.message,
                    details=details,
                ) from ensure_exc
            return self._resolve(model)

    async def complete(
        self,
        *,
        request_id: str,
        body: ChatCompletionRequest,
        principal_subject: str | None = None,
    ) -> ChatCompletionResponse:
        if not self._accepting_requests():
            raise ChatServiceError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Gateway is shutting down and not accepting new requests.",
            )
        resolved = await self._resolve_ready(
            body.model,
            request_id=request_id,
            principal_subject=principal_subject,
        )
        prepared = await self._prepare_context(
            request_id=request_id,
            body=body,
            deployment=resolved.deployment,
            principal_subject=principal_subject,
        )
        self._admit_size(
            resolved.deployment, prepared.messages, body.max_tokens
        )
        provider = self._provider_for(resolved.deployment.provider)
        chat_request = self._to_chat_request(
            request_id=request_id,
            body=body,
            deployment_id=resolved.deployment.id,
            messages=prepared.messages,
        )
        try:
            await self.gate.acquire(resolved.deployment)
        except GateRejection as exc:
            raise ChatServiceError(exc.code, exc.message, details=exc.details) from exc

        created = int(time.time())
        completion_id = f"chatcmpl_{request_id}"
        parts: list[str] = []
        finish_reason = "stop"
        usage: ChatCompletionUsage | None = None
        try:
            chunks = await self._collect_with_timeout(provider, chat_request)
            for chunk in chunks:
                if chunk.delta_content:
                    parts.append(chunk.delta_content)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
            usage = self._usage_from_chunks(chunks)
        except ChatServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ChatServiceError(
                ErrorCode.PROVIDER_FAILURE,
                "Inference provider failed while generating a response.",
                details={"reason": str(exc), "provider": resolved.deployment.provider},
            ) from exc
        finally:
            await self.gate.release(resolved.deployment)
            if self._on_state_changed:
                self._on_state_changed()

        content = "".join(parts)
        return ChatCompletionResponse(
            id=completion_id,
            created=created,
            model=body.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ApiChatMessage(role="assistant", content=content),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
        )

    async def stream(
        self,
        *,
        request_id: str,
        body: ChatCompletionRequest,
        principal_subject: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield OpenAI-compatible SSE lines plus optional yonder.context events."""

        if not self._accepting_requests():
            raise ChatServiceError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Gateway is shutting down and not accepting new requests.",
            )
        resolved = await self._resolve_ready(
            body.model,
            request_id=request_id,
            principal_subject=principal_subject,
        )
        prepared = await self._prepare_context(
            request_id=request_id,
            body=body,
            deployment=resolved.deployment,
            principal_subject=principal_subject,
        )
        # Always emit a context meter event so the console can render pressure.
        yield f"data: {prepared.event.model_dump_json()}\n\n"

        self._admit_size(
            resolved.deployment, prepared.messages, body.max_tokens
        )
        provider = self._provider_for(resolved.deployment.provider)
        chat_request = self._to_chat_request(
            request_id=request_id,
            body=body,
            deployment_id=resolved.deployment.id,
            messages=prepared.messages,
        )
        try:
            await self.gate.acquire(resolved.deployment)
        except GateRejection as exc:
            raise ChatServiceError(exc.code, exc.message, details=exc.details) from exc

        created = int(time.time())
        completion_id = f"chatcmpl_{request_id}"
        sent_role = False
        deadline = time.monotonic() + self.admission.inference_timeout_seconds
        chunks = provider.chat(chat_request).__aiter__()
        final_usage: ChatCompletionUsage | None = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ChatServiceError(
                        ErrorCode.INFERENCE_TIMEOUT,
                        "Inference exceeded the configured timeout.",
                        details={
                            "inference_timeout_seconds": str(
                                self.admission.inference_timeout_seconds
                            )
                        },
                    )
                try:
                    chunk = await asyncio.wait_for(anext(chunks), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise ChatServiceError(
                        ErrorCode.INFERENCE_TIMEOUT,
                        "Inference exceeded the configured timeout.",
                        details={
                            "inference_timeout_seconds": str(
                                self.admission.inference_timeout_seconds
                            )
                        },
                    ) from exc
                if chunk.usage is not None:
                    final_usage = ChatCompletionUsage(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    )
                delta = ChatCompletionChunkDelta()
                if not sent_role and chunk.delta_content is not None:
                    delta.role = "assistant"
                    sent_role = True
                if chunk.delta_content is not None:
                    delta.content = chunk.delta_content
                if (
                    chunk.delta_content is None
                    and chunk.finish_reason is None
                    and chunk.usage is not None
                ):
                    # Usage-only terminal chunk from the provider.
                    continue
                payload = ChatCompletionChunk(
                    id=completion_id,
                    created=created,
                    model=body.model,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=chunk.index,
                            delta=delta,
                            finish_reason=chunk.finish_reason,
                        )
                    ],
                    usage=final_usage if chunk.finish_reason else None,
                )
                yield f"data: {payload.model_dump_json()}\n\n"

            if final_usage is not None:
                upgraded = meter_with_provider_usage(
                    prepared.meter,
                    prompt_tokens=final_usage.prompt_tokens,
                )
                yield (
                    "data: "
                    + prepared.event.model_copy(
                        update={
                            "meter": upgraded,
                            "condensed": prepared.condensed,
                            "messages": prepared.event.messages
                            if prepared.condensed
                            else [],
                        }
                    ).model_dump_json()
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"
        except ChatServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ChatServiceError(
                ErrorCode.PROVIDER_FAILURE,
                "Inference provider failed while streaming a response.",
                details={"reason": str(exc), "provider": resolved.deployment.provider},
            ) from exc
        finally:
            await self.gate.release(resolved.deployment)
            if self._on_state_changed:
                self._on_state_changed()
