"""Live Lab execution — prefer ready chat aliases; fall back to training mocks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import Request
from yonder_contracts.tools import ToolCall

from app.models.openai import ChatCompletionRequest, ChatMessage, ResponseFormat
from app.paths import resolve_repo_root
from app.routing.resolve import RouteResolutionError, resolve_route
from app.services.chat import ChatServiceError
from app.services.lab_scenarios import (
    GUIDED_PROMPTS,
    RUBRIC_SCHEMA,
    GuidedMode,
    MediaKind,
    load_corpus,
    retrieve,
    run_ability,
)
from app.services.tools import build_default_tool_executor


def runtime_config_path() -> Path:
    return resolve_repo_root() / "config" / "lab" / "runtime.yaml"


def load_runtime_config() -> dict[str, Any]:
    path = runtime_config_path()
    defaults = {
        "chat_alias": "yonder-chat",
        "rag_alias": "yonder-chat",
        "structured_alias": "yonder-chat",
        "tools_alias": "yonder-chat",
        "generation_enabled": False,
    }
    if not path.exists():
        return defaults
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "chat_alias": str(raw.get("chat_alias") or defaults["chat_alias"]),
        "rag_alias": str(raw.get("rag_alias") or defaults["rag_alias"]),
        "structured_alias": str(
            raw.get("structured_alias") or defaults["structured_alias"]
        ),
        "tools_alias": str(raw.get("tools_alias") or defaults["tools_alias"]),
        "generation_enabled": bool(
            raw.get("generation_enabled", defaults["generation_enabled"])
        ),
    }


def alias_is_ready(request: Request, alias: str) -> bool:
    resolved = resolve_route(request.app.state.routes, alias)
    return not isinstance(resolved, RouteResolutionError)


def _teaching_for(ability: str) -> dict[str, list[str]]:
    common_build = [
        "Keep traffic on the Gateway — never call workers directly.",
        "Prefer stable aliases; treat readiness as explicit, never invented.",
    ]
    mapping: dict[str, tuple[list[str], list[str]]] = {
        "guided": (
            [
                "A guided mode is a productized system prompt on the chat pipeline.",
                "Live mode uses the configured chat alias when READY.",
            ],
            common_build,
        ),
        "structured": (
            [
                "Structured mode asks for schema-valid JSON and validates fail-closed.",
                "Live mode uses response_format when the provider supports it.",
            ],
            common_build,
        ),
        "tools": (
            [
                "Tool use: plan → tool_call → tool_result → answer.",
                "Live mode executes the weather tool then asks the chat alias to ground.",
            ],
            common_build,
        ),
        "rag": (
            [
                "RAG retrieves trusted chunks, then generates a grounded answer.",
                "Live mode retrieves from the Lab corpus then calls the chat alias.",
            ],
            common_build,
        ),
    }
    how, build = mapping.get(ability, (["Lab ability"], common_build))
    return {"how_it_works": how, "how_to_build": build}


def _mock_fallback(
    ability: str,
    user_input: str,
    *,
    guided_mode: GuidedMode | None,
    media_kind: MediaKind | None,
    generation: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    fallback = run_ability(
        ability,  # type: ignore[arg-type]
        user_input,
        guided_mode=guided_mode,
        media_kind=media_kind,
        generation=generation,
    )
    result = dict(fallback)
    result["mode"] = "training_scenario"
    result["provider"] = "mock"
    steps = list(result.get("steps") or [])
    steps.insert(
        0,
        {
            "type": "live_fallback",
            "title": "Live alias unavailable",
            "content": reason,
        },
    )
    result["steps"] = steps
    return result


async def _chat_complete(
    request: Request,
    *,
    request_id: str,
    alias: str,
    messages: list[ChatMessage],
    response_format: ResponseFormat | None = None,
    max_tokens: int = 512,
) -> str:
    body = ChatCompletionRequest(
        model=alias,
        messages=messages,
        stream=False,
        max_tokens=max_tokens,
        temperature=0.2,
        response_format=response_format,
    )
    response = await request.app.state.chat_service.complete(
        request_id=request_id,
        body=body,
    )
    choice = (response.choices or [None])[0]
    if choice is None or choice.message is None:
        return ""
    return str(choice.message.content or "")


async def run_ability_live_or_mock(
    request: Request,
    *,
    request_id: str,
    ability: str,
    user_input: str,
    guided_mode: GuidedMode | None = None,
    media_kind: MediaKind | None = None,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prefer live chat when alias is READY; otherwise training_scenario mock."""
    cfg = load_runtime_config()

    if ability == "generation":
        return run_ability(
            "generation",
            user_input,
            media_kind=media_kind,
            generation=generation,
        )

    try:
        if ability == "guided":
            mode: GuidedMode = guided_mode or "summarize"
            alias = str(cfg["chat_alias"])
            if not alias_is_ready(request, alias):
                return _mock_fallback(
                    "guided",
                    user_input,
                    guided_mode=mode,
                    media_kind=media_kind,
                    generation=generation,
                    reason=f"alias not ready: {alias}",
                )
            answer = await _chat_complete(
                request,
                request_id=request_id,
                alias=alias,
                messages=[
                    ChatMessage(role="system", content=GUIDED_PROMPTS[mode]),
                    ChatMessage(role="user", content=user_input),
                ],
            )
            return {
                "ability": "guided",
                "mode": "live",
                "provider": "gateway_chat",
                "guided_mode": mode,
                "steps": [
                    {
                        "type": "system_prompt",
                        "title": f"Live guided mode: {mode}",
                        "content": GUIDED_PROMPTS[mode],
                    },
                    {"type": "answer", "content": answer},
                ],
                "answer": answer,
                "citations": [],
                "structured": None,
                "assets": [],
                "teaching": _teaching_for("guided"),
            }

        if ability == "structured":
            alias = str(cfg["structured_alias"])
            if not alias_is_ready(request, alias):
                return _mock_fallback(
                    "structured",
                    user_input,
                    guided_mode=guided_mode,
                    media_kind=media_kind,
                    generation=generation,
                    reason=f"alias not ready: {alias}",
                )
            schema_prompt = (
                "Return ONLY JSON matching this schema (no markdown):\n"
                f"{RUBRIC_SCHEMA}\n"
                "Grade the intake text the user provides."
            )
            answer = await _chat_complete(
                request,
                request_id=request_id,
                alias=alias,
                messages=[
                    ChatMessage(role="system", content=schema_prompt),
                    ChatMessage(role="user", content=user_input),
                ],
                response_format=ResponseFormat(
                    type="json_schema",
                    json_schema=RUBRIC_SCHEMA,
                    name="yonder_lab_rubric",
                    strict=True,
                ),
                max_tokens=800,
            )
            structured: dict[str, Any] | None = None
            try:
                parsed = json.loads(answer)
                if isinstance(parsed, dict):
                    structured = parsed
            except json.JSONDecodeError:
                structured = None
            return {
                "ability": "structured",
                "mode": "live",
                "provider": "gateway_chat",
                "steps": [
                    {
                        "type": "schema",
                        "title": "Rubric schema",
                        "content": RUBRIC_SCHEMA,
                    },
                    {"type": "answer", "content": answer},
                ],
                "answer": answer,
                "citations": [],
                "structured": structured,
                "assets": [],
                "teaching": _teaching_for("structured"),
            }

        if ability == "tools":
            alias = str(cfg["tools_alias"])
            if not alias_is_ready(request, alias):
                return _mock_fallback(
                    "tools",
                    user_input,
                    guided_mode=guided_mode,
                    media_kind=media_kind,
                    generation=generation,
                    reason=f"alias not ready: {alias}",
                )
            city = "Seattle"
            lowered = user_input.lower()
            for candidate in ("seattle", "portland", "austin", "denver", "boston"):
                if candidate in lowered:
                    city = candidate.title()
                    break
            executor = build_default_tool_executor()
            executed = executor.execute(
                ToolCall(
                    id="lab-live-weather-1",
                    name="lookup_weather",
                    arguments={"city": city},
                )
            )
            try:
                tool_result: Any = json.loads(executed.content)
            except json.JSONDecodeError:
                tool_result = {"raw": executed.content}
            answer = await _chat_complete(
                request,
                request_id=request_id,
                alias=alias,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You answer using the provided tool_result only. "
                            "Do not invent weather observations."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"User asked: {user_input}\n"
                            f"tool_result lookup_weather({city})={tool_result}"
                        ),
                    ),
                ],
            )
            return {
                "ability": "tools",
                "mode": "live",
                "provider": "gateway_chat",
                "steps": [
                    {
                        "type": "tool_call",
                        "title": "lookup_weather",
                        "content": {"city": city},
                    },
                    {
                        "type": "tool_result",
                        "title": "lookup_weather result",
                        "content": tool_result,
                    },
                    {"type": "answer", "content": answer},
                ],
                "answer": answer,
                "citations": [],
                "structured": None,
                "assets": [],
                "teaching": _teaching_for("tools"),
            }

        if ability == "rag":
            alias = str(cfg["rag_alias"])
            if not alias_is_ready(request, alias):
                return _mock_fallback(
                    "rag",
                    user_input,
                    guided_mode=guided_mode,
                    media_kind=media_kind,
                    generation=generation,
                    reason=f"alias not ready: {alias}",
                )
            docs = retrieve(user_input, k=3)
            if not docs:
                docs = load_corpus()[:3]
            citations = [
                {"id": doc.id, "title": doc.title, "snippet": doc.text[:240]}
                for doc in docs
            ]
            context = "\n\n".join(
                f"[{doc.id}] {doc.title}\n{doc.text}" for doc in docs
            )
            answer = await _chat_complete(
                request,
                request_id=request_id,
                alias=alias,
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "Answer using ONLY the provided context chunks. "
                            "Cite document ids in brackets. If missing, say not in corpus."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=f"Context:\n{context}\n\nQuestion: {user_input}",
                    ),
                ],
                max_tokens=700,
            )
            return {
                "ability": "rag",
                "mode": "live",
                "provider": "gateway_chat",
                "steps": [
                    {
                        "type": "retrieve",
                        "title": "Lab corpus retrieve",
                        "content": [c["id"] for c in citations],
                    },
                    {"type": "answer", "content": answer},
                ],
                "answer": answer,
                "citations": citations,
                "structured": None,
                "assets": [],
                "teaching": _teaching_for("rag"),
            }
    except ChatServiceError as exc:
        return _mock_fallback(
            ability,
            user_input,
            guided_mode=guided_mode,
            media_kind=media_kind,
            generation=generation,
            reason=exc.message,
        )

    return run_ability(
        ability,  # type: ignore[arg-type]
        user_input,
        guided_mode=guided_mode,
        media_kind=media_kind,
        generation=generation,
    )
