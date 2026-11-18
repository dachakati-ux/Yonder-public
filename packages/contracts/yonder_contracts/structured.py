"""Structured output / JSON-schema response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredResponseFormat:
    """OpenAI-style response_format constraint (json_schema / json_object)."""

    type: str  # json_object | json_schema
    json_schema: dict[str, Any] | None = None
    name: str | None = None
    strict: bool = True


@dataclass(frozen=True)
class SchemaRegistryEntry:
    schema_id: str
    version: str
    title: str
    json_schema: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class StructuredValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    parsed: dict[str, Any] | list[Any] | None = None
    schema_id: str | None = None
