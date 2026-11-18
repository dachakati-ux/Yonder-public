"""Allowlisted Gateway tool registry and executor."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from yonder_contracts.tools import ToolCall, ToolDefinition, ToolResult

ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolExecutor:
    """Execute allowlisted tools inside the Gateway — never from the console."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._tools[definition.name] = RegisteredTool(definition=definition, handler=handler)

    def list_definitions(self) -> list[ToolDefinition]:
        return [item.definition for item in self._tools.values()]

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def execute(self, call: ToolCall) -> ToolResult:
        registered = self._tools.get(call.name)
        if registered is None:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=json.dumps({"error": "tool_not_allowlisted", "name": call.name}),
                is_error=True,
            )
        missing = _required_missing(registered.definition.parameters_schema, call.arguments)
        if missing:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=json.dumps({"error": "invalid_arguments", "missing": missing}),
                is_error=True,
            )
        try:
            content = registered.handler(dict(call.arguments))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=json.dumps({"error": "tool_execution_failed", "detail": str(exc)}),
                is_error=True,
            )
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=content,
            is_error=False,
        )


def _required_missing(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return []
    return [str(name) for name in required if name not in arguments]


def build_default_tool_executor() -> ToolExecutor:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="lookup_weather",
            description="Return a scripted weather observation for a city (training/mock only).",
            parameters_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        ),
        _lookup_weather,
    )
    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo a string payload (mock tool for contract tests).",
            parameters_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        lambda args: json.dumps({"echo": str(args.get("text", "")), "provider": "mock"}),
    )
    executor.register(
        ToolDefinition(
            name="yonder_fixture_get_ticket",
            description=(
                "Fetch a sanitized teaching ticket by id (fixture only). "
                "Availability is mock — not a live ticketing connector."
            ),
            parameters_schema={
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
        ),
        _fixture_get_ticket,
    )
    executor.register(
        ToolDefinition(
            name="yonder_fixture_get_runbook",
            description=(
                "Fetch a sanitized teaching runbook by topic (fixture only). "
                "Availability is mock — not a live runbook system."
            ),
            parameters_schema={
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        ),
        _fixture_get_runbook,
    )
    return executor


def _lookup_weather(args: dict[str, Any]) -> str:
    city = str(args.get("city", "unknown")).strip() or "unknown"
    return json.dumps(
        {
            "city": city,
            "condition": "clear",
            "temp_c": 21,
            "source": "yonder-mock-tool",
            "availability": "mock",
            "note": "Scripted observation for training; not live weather data.",
        }
    )


_FIXTURE_TICKETS = {
    "FIX-1001": {
        "ticket_id": "FIX-1001",
        "subject": "VPN flapping on lab edge",
        "body": (
            "Users drop for 30-90s after Friday firewall change. "
            "Impact count not_reported. Do not invent customer names."
        ),
        "severity_hint": "unknown",
        "availability": "mock",
        "source": "yonder-fixture-ticketing",
    }
}

_FIXTURE_RUNBOOKS = {
    "vpn-flap": {
        "topic": "vpn-flap",
        "title": "Teaching runbook — VPN flap",
        "steps": [
            "Confirm change window and recent firewall changes from evidence.",
            "Check edge health; do not invent packet loss percentages.",
            "If impact is not_reported, keep severity unknown until measured.",
        ],
        "availability": "mock",
        "source": "yonder-fixture-runbook",
    }
}


def _fixture_get_ticket(args: dict[str, Any]) -> str:
    ticket_id = str(args.get("ticket_id", "")).strip()
    ticket = _FIXTURE_TICKETS.get(ticket_id)
    if ticket is None:
        return json.dumps(
            {
                "error": "not_found",
                "ticket_id": ticket_id,
                "availability": "mock",
                "known_ids": sorted(_FIXTURE_TICKETS),
                "note": "Fixture only — unknown ids are not invented.",
            }
        )
    return json.dumps(ticket)


def _fixture_get_runbook(args: dict[str, Any]) -> str:
    topic = str(args.get("topic", "")).strip().lower()
    book = _FIXTURE_RUNBOOKS.get(topic)
    if book is None:
        return json.dumps(
            {
                "error": "not_found",
                "topic": topic,
                "availability": "mock",
                "known_topics": sorted(_FIXTURE_RUNBOOKS),
                "note": "Fixture only — unknown topics are not invented.",
            }
        )
    return json.dumps(book)
