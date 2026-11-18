"""Gateway-owned context pressure measurement and auto-condensation.

Pre-request pressure uses a labeled char÷N estimate against deployment
`context_length`. That estimate is never exported as authoritative
`prompt_tokens`. Provider usage is forwarded separately when available.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from yonder_configuration.models import ContextWindowConfig

from app.models.openai import (
    ChatMessage as ApiChatMessage,
)
from app.models.openai import (
    YonderContextEvent,
    YonderContextMeter,
)
from app.routing.admission import estimate_prompt_chars
from app.routing.models import DeploymentRecord
from app.services.conversations import ConversationMessage, ConversationStore

SUMMARY_SYSTEM = (
    "You are Yonder's context condenser. Summarize the conversation turns "
    "below for continued chat. Preserve decisions, identifiers, constraints, "
    "and open questions. Do not invent facts. Reply with the summary only."
)


@dataclass(frozen=True)
class ContextPrepareResult:
    messages: list[ApiChatMessage]
    meter: YonderContextMeter
    condensed: bool
    event: YonderContextEvent


SummarizeFn = Callable[[list[ApiChatMessage]], Awaitable[str]]


def deployment_context_length(deployment: DeploymentRecord) -> int | None:
    raw = (deployment.runtime_configuration or {}).get("context_length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def measure_pressure(
    *,
    messages: list[ApiChatMessage] | list[object],
    deployment: DeploymentRecord,
    config: ContextWindowConfig,
    max_tokens: int | None,
    admission_output_tokens: int,
) -> YonderContextMeter:
    prompt_chars = estimate_prompt_chars(messages)
    context_length = deployment_context_length(deployment)
    if context_length is None:
        return YonderContextMeter(
            context_length=None,
            context_length_availability="not_reported",
            prompt_chars=prompt_chars,
            estimate_prompt_tokens=None,
            pressure_ratio=None,
            pressure_availability="not_reported",
            prompt_tokens=None,
            soft_ratio=config.soft_ratio,
            should_condense=False,
        )

    estimate = math.ceil(prompt_chars / config.chars_per_token_estimate)
    reserve = max_tokens if max_tokens is not None else min(admission_output_tokens, 1024)
    ratio = (estimate + reserve) / float(context_length)
    return YonderContextMeter(
        context_length=context_length,
        context_length_availability="available",
        prompt_chars=prompt_chars,
        estimate_prompt_tokens=estimate,
        pressure_ratio=ratio,
        pressure_availability="estimate",
        prompt_tokens=None,
        soft_ratio=config.soft_ratio,
        should_condense=ratio >= config.soft_ratio,
    )


def split_for_condensation(
    messages: list[ApiChatMessage],
    *,
    keep_recent_messages: int,
) -> tuple[list[ApiChatMessage], list[ApiChatMessage], list[ApiChatMessage]] | None:
    """Return (leading_system, middle, recent) or None when nothing to drop."""

    leading: list[ApiChatMessage] = []
    index = 0
    while index < len(messages) and messages[index].role == "system":
        leading.append(messages[index])
        index += 1
    body = messages[index:]
    if len(body) <= keep_recent_messages:
        return None
    middle = body[:-keep_recent_messages]
    recent = body[-keep_recent_messages:]
    if not middle:
        return None
    return leading, middle, recent


def _now() -> datetime:
    return datetime.now(UTC)


class ContextWindowService:
    def __init__(
        self,
        *,
        config: ContextWindowConfig,
        store: ConversationStore | None = None,
        admission_output_tokens: int = 4096,
    ) -> None:
        self.config = config
        self.store = store
        self.admission_output_tokens = admission_output_tokens

    async def prepare(
        self,
        *,
        messages: list[ApiChatMessage],
        deployment: DeploymentRecord,
        max_tokens: int | None,
        skip_condense: bool,
        conversation_id: str | None,
        principal_subject: str | None,
        summarize: SummarizeFn,
    ) -> ContextPrepareResult:
        meter = measure_pressure(
            messages=messages,
            deployment=deployment,
            config=self.config,
            max_tokens=max_tokens,
            admission_output_tokens=self.admission_output_tokens,
        )

        if (
            not self.config.enabled
            or skip_condense
            or not meter.should_condense
        ):
            event = YonderContextEvent(
                condensed=False,
                meter=meter,
                messages=[],
            )
            return ContextPrepareResult(
                messages=list(messages),
                meter=meter,
                condensed=False,
                event=event,
            )

        split = split_for_condensation(
            messages, keep_recent_messages=self.config.keep_recent_messages
        )
        if split is None:
            event = YonderContextEvent(condensed=False, meter=meter, messages=[])
            return ContextPrepareResult(
                messages=list(messages),
                meter=meter,
                condensed=False,
                event=event,
            )

        leading, middle, recent = split
        summary_text = (await summarize(middle)).strip()
        if not summary_text:
            event = YonderContextEvent(condensed=False, meter=meter, messages=[])
            return ContextPrepareResult(
                messages=list(messages),
                meter=meter,
                condensed=False,
                event=event,
            )

        summary_message = ApiChatMessage(
            role="system",
            content=f"Yonder context summary (auto): {summary_text}",
        )
        condensed_messages = [*leading, summary_message, *recent]
        post_meter = measure_pressure(
            messages=condensed_messages,
            deployment=deployment,
            config=self.config,
            max_tokens=max_tokens,
            admission_output_tokens=self.admission_output_tokens,
        )
        # After condensation pressure is still an estimate.
        post_meter = post_meter.model_copy(update={"should_condense": False})

        ui_messages = await self._persist_and_ui_messages(
            condensed_messages=condensed_messages,
            conversation_id=conversation_id,
            principal_subject=principal_subject,
        )
        event = YonderContextEvent(
            condensed=True,
            meter=post_meter,
            messages=ui_messages,
        )
        return ContextPrepareResult(
            messages=condensed_messages,
            meter=post_meter,
            condensed=True,
            event=event,
        )

    async def _persist_and_ui_messages(
        self,
        *,
        condensed_messages: list[ApiChatMessage],
        conversation_id: str | None,
        principal_subject: str | None,
    ) -> list[dict[str, str]]:
        store_rows: list[ConversationMessage] = []
        now = _now()
        convo_id = conversation_id or "ephemeral"
        for message in condensed_messages:
            role = message.role
            if role not in ("system", "user", "assistant"):
                continue
            content = message.content or ""
            if not content:
                continue
            store_rows.append(
                ConversationMessage(
                    id=f"msg_{uuid.uuid4().hex[:16]}",
                    conversation_id=convo_id,
                    role=role,  # type: ignore[arg-type]
                    content=content,
                    request_id=None,
                    created_at=now,
                )
            )

        if (
            self.store is not None
            and conversation_id
            and principal_subject
        ):
            replaced = await self.store.replace_messages(
                conversation_id,
                principal_subject,
                messages=[
                    {
                        "role": row.role,
                        "content": row.content,
                        "request_id": row.request_id,
                    }
                    for row in store_rows
                    # Persist user/assistant + auto summary system; skip empty.
                    if row.content
                ],
            )
            if replaced is not None:
                store_rows = replaced

        return [
            {"id": row.id, "role": row.role, "content": row.content}
            for row in store_rows
        ]

    def build_summary_prompt(
        self, middle: list[ApiChatMessage]
    ) -> list[ApiChatMessage]:
        transcript_lines: list[str] = []
        for message in middle:
            transcript_lines.append(
                f"{message.role}: {message.content or ''}".strip()
            )
        transcript = "\n\n".join(transcript_lines)
        return [
            ApiChatMessage(role="system", content=SUMMARY_SYSTEM),
            ApiChatMessage(
                role="user",
                content=f"Conversation turns to summarize:\n\n{transcript}",
            ),
        ]


def meter_with_provider_usage(
    meter: YonderContextMeter,
    *,
    prompt_tokens: int | None,
) -> YonderContextMeter:
    """Upgrade an estimate meter when authoritative provider counts arrive."""

    if prompt_tokens is None or meter.context_length is None:
        return meter
    ratio = prompt_tokens / float(meter.context_length)
    return meter.model_copy(
        update={
            "prompt_tokens": prompt_tokens,
            "pressure_ratio": ratio,
            "pressure_availability": "provider",
            "should_condense": False,
        }
    )
