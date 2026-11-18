"""OpenAI-compatible adapter for llama.cpp servers (static or worker-managed)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from yonder_configuration.models import LlamaCppProviderConfig
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.inference import (
    ChatChunk,
    ChatRequest,
    ChatUsage,
    Deployment,
    DeploymentSpec,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderModel,
)
from yonder_contracts.tools import ToolCall


class LlamaCppProvider:
    """Normalize llama.cpp's OpenAI API behind Yonder's provider contract.

    Supports:
    - Static lab escape hatch via config.base_url (operator-managed unit)
    - Per-deployment endpoints returned by the worker runtime manager
    """

    name = "llama-cpp"

    def __init__(
        self,
        config: LlamaCppProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        # deployment_id -> served model id
        self._loaded: dict[str, str] = {}
        # deployment_id -> base_url
        self._endpoints: dict[str, str] = {}

    def _client(self, base_url: str | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=(base_url or self._config.base_url).rstrip("/"),
            timeout=self._config.request_timeout_seconds,
            transport=self._transport,
            headers=worker_auth_headers(),
        )

    def _endpoint_for(self, deployment_id: str | None = None) -> str:
        if deployment_id and deployment_id in self._endpoints:
            return self._endpoints[deployment_id]
        return self._config.base_url

    async def health(self) -> ProviderHealth:
        endpoints = list(dict.fromkeys([self._config.base_url, *self._endpoints.values()]))
        details: dict[str, Any] = {
            "endpoints": endpoints,
            "loaded_deployments": list(self._loaded),
        }
        try:
            async with self._client() as client:
                response = await client.get("/health")
                response.raise_for_status()
            return ProviderHealth(
                status=ProviderHealthStatus.HEALTHY,
                provider=self.name,
                message="llama.cpp runtime is reachable",
                details={**details, "availability": "available"},
            )
        except (httpx.HTTPError, ValueError) as exc:
            # Still healthy if any managed endpoint is up.
            for endpoint in self._endpoints.values():
                try:
                    async with self._client(endpoint) as client:
                        response = await client.get("/health")
                        response.raise_for_status()
                    return ProviderHealth(
                        status=ProviderHealthStatus.HEALTHY,
                        provider=self.name,
                        message="At least one managed llama.cpp endpoint is reachable",
                        details={**details, "availability": "available"},
                    )
                except (httpx.HTTPError, ValueError):
                    continue
            return ProviderHealth(
                status=ProviderHealthStatus.UNHEALTHY,
                provider=self.name,
                message="llama.cpp runtime is unavailable",
                details={
                    **details,
                    "availability": "collection_failed",
                    "error_type": type(exc).__name__,
                },
            )

    async def discover_models(self, *, base_url: str | None = None) -> list[ProviderModel]:
        async with self._client(base_url) as client:
            response = await client.get("/v1/models")
            response.raise_for_status()
            payload = response.json()

        models: list[ProviderModel] = []
        for item in payload.get("data", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            models.append(
                ProviderModel(
                    id=model_id,
                    display_name=model_id,
                    owned_by=str(item.get("owned_by") or "llama.cpp"),
                    capabilities=["chat", "streaming"],
                )
            )
        return models

    async def load(self, deployment: DeploymentSpec) -> Deployment:
        endpoint = (deployment.worker_endpoint or self._config.base_url).rstrip("/")
        if deployment.worker_endpoint:
            # Worker-managed lane: the worker spawned this server for this
            # deployment, so the manifest identifier is authoritative.
            # config.served_model describes only the static operator endpoint and
            # must not gate other deployments.
            expected_model = deployment.model_identifier.strip()
        else:
            expected_model = (
                self._config.served_model or deployment.model_identifier
            ).strip()
        discovered = await self.discover_models(base_url=endpoint)
        available_ids = {model.id for model in discovered}
        if expected_model not in available_ids:
            raise RuntimeError(
                "Configured llama.cpp model is not being served "
                f"(expected={expected_model!r}, available={sorted(available_ids)!r}, "
                f"endpoint={endpoint!r})"
            )

        self._loaded[deployment.deployment_id] = expected_model
        self._endpoints[deployment.deployment_id] = endpoint
        lifecycle = (
            "worker_managed" if deployment.worker_endpoint else "operator_managed"
        )
        return Deployment(
            deployment_id=deployment.deployment_id,
            status="ready",
            provider=self.name,
            details={
                "model": expected_model,
                "runtime_lifecycle": lifecycle,
                "base_url": endpoint,
                "gpu_assignments": list(deployment.gpu_assignments or []),
                "placement": deployment.placement,
            },
        )

    async def unload(self, deployment_id: str) -> None:
        self._loaded.pop(deployment_id, None)
        self._endpoints.pop(deployment_id, None)

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        out = {
            key: value
            for key, value in {
                "role": message.role,
                "content": message.content,
                "name": message.name,
                "tool_call_id": message.tool_call_id,
            }.items()
            if value is not None
        }
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments or {})),
                    },
                }
                for call in tool_calls
            ]
        return out

    @staticmethod
    def _tool_payload(request: ChatRequest) -> list[dict[str, Any]] | None:
        if not request.tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in request.tools
        ]

    @staticmethod
    def _response_format_payload(request: ChatRequest) -> dict[str, Any] | None:
        response_format = request.response_format
        if response_format is None:
            return None
        if response_format.type == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format.name or "yonder_response",
                    "schema": response_format.json_schema or {},
                    "strict": response_format.strict,
                },
            }
        return {"type": response_format.type}

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        model = self._loaded.get(request.deployment_id)
        if model is None:
            raise RuntimeError(
                f"Deployment is not loaded in llama.cpp provider: {request.deployment_id}"
            )
        endpoint = self._endpoint_for(request.deployment_id)

        payload: dict[str, Any] = {
            "model": model,
            "messages": [self._message_payload(message) for message in request.messages],
            "stream": True,
            # llama.cpp OpenAI server emits usage on the final SSE chunk when set.
            "stream_options": {"include_usage": True},
        }
        optional = {
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.max_tokens,
            "stop": list(request.stop) if request.stop else None,
            "tools": self._tool_payload(request),
            "tool_choice": request.tool_choice,
            "response_format": self._response_format_payload(request),
        }
        payload.update({key: value for key, value in optional.items() if value is not None})

        tool_fragments: dict[int, dict[str, str]] = {}
        async with self._client(endpoint) as client:
            async with client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    usage_payload = event.get("usage")
                    usage: ChatUsage | None = None
                    if isinstance(usage_payload, dict):
                        try:
                            prompt_tokens = int(usage_payload.get("prompt_tokens") or 0)
                            completion_tokens = int(
                                usage_payload.get("completion_tokens") or 0
                            )
                            total_tokens = int(
                                usage_payload.get("total_tokens")
                                or (prompt_tokens + completion_tokens)
                            )
                            usage = ChatUsage(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=total_tokens,
                            )
                        except (TypeError, ValueError):
                            usage = None
                    # Fallback: llama.cpp timings block (prompt_n / predicted_n).
                    if usage is None and isinstance(event.get("timings"), dict):
                        timings = event["timings"]
                        try:
                            prompt_tokens = int(timings.get("prompt_n") or 0)
                            completion_tokens = int(timings.get("predicted_n") or 0)
                            if prompt_tokens or completion_tokens:
                                usage = ChatUsage(
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    total_tokens=prompt_tokens + completion_tokens,
                                )
                        except (TypeError, ValueError):
                            usage = None

                    emitted = False
                    for choice in event.get("choices", []):
                        index = int(choice.get("index", 0))
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content in (None, ""):
                            reasoning = delta.get("reasoning_content")
                            if reasoning:
                                content = reasoning
                        for fragment in delta.get("tool_calls") or []:
                            tool_index = int(fragment.get("index", 0))
                            current = tool_fragments.setdefault(
                                tool_index, {"id": "", "name": "", "arguments": ""}
                            )
                            current["id"] += str(fragment.get("id") or "")
                            function = fragment.get("function") or {}
                            current["name"] += str(function.get("name") or "")
                            current["arguments"] += str(function.get("arguments") or "")

                        finish_reason = choice.get("finish_reason")
                        calls: tuple[ToolCall, ...] | None = None
                        if finish_reason and tool_fragments:
                            parsed: list[ToolCall] = []
                            for tool_index in sorted(tool_fragments):
                                item = tool_fragments[tool_index]
                                try:
                                    arguments = json.loads(item["arguments"] or "{}")
                                except json.JSONDecodeError:
                                    arguments = {"_raw": item["arguments"]}
                                parsed.append(
                                    ToolCall(
                                        id=item["id"] or f"call_{request.request_id}_{tool_index}",
                                        name=item["name"],
                                        arguments=arguments,
                                    )
                                )
                            calls = tuple(parsed)

                        if content is not None or finish_reason is not None or calls:
                            yield ChatChunk(
                                request_id=request.request_id,
                                deployment_id=request.deployment_id,
                                model=request.model_alias,
                                delta_content=content,
                                finish_reason=finish_reason,
                                index=index,
                                tool_calls=calls,
                                usage=usage if finish_reason else None,
                            )
                            emitted = True
                    if usage is not None and not emitted:
                        yield ChatChunk(
                            request_id=request.request_id,
                            deployment_id=request.deployment_id,
                            model=request.model_alias,
                            usage=usage,
                        )
