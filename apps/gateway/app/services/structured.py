"""Versioned JSON schema registry and fail-closed structured-output validation."""

from __future__ import annotations

import json
from typing import Any

from yonder_contracts.structured import (
    SchemaRegistryEntry,
    StructuredResponseFormat,
    StructuredValidationResult,
)


class SchemaRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SchemaRegistryEntry] = {}

    def register(self, entry: SchemaRegistryEntry) -> None:
        self._entries[entry.schema_id] = entry

    def get(self, schema_id: str) -> SchemaRegistryEntry | None:
        return self._entries.get(schema_id)

    def list(self) -> list[SchemaRegistryEntry]:
        return sorted(self._entries.values(), key=lambda item: item.schema_id)


def validate_structured_content(
    content: str,
    *,
    response_format: StructuredResponseFormat | None = None,
    schema: dict[str, Any] | None = None,
    schema_id: str | None = None,
) -> StructuredValidationResult:
    """Fail closed: invalid JSON or schema mismatch returns valid=False."""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return StructuredValidationResult(
            valid=False,
            errors=(f"invalid_json: {exc.msg}",),
            schema_id=schema_id,
        )

    effective_schema = schema
    if (
        response_format is not None
        and response_format.type == "json_schema"
        and response_format.json_schema
    ):
        effective_schema = response_format.json_schema
        schema_id = schema_id or response_format.name

    if effective_schema is None:
        if not isinstance(parsed, (dict, list)):
            return StructuredValidationResult(
                valid=False,
                errors=("json_object_expected",),
                schema_id=schema_id,
            )
        return StructuredValidationResult(
            valid=True,
            parsed=parsed,
            schema_id=schema_id,
        )

    errors = _validate_against_schema(parsed, effective_schema, path="$")
    return StructuredValidationResult(
        valid=not errors,
        errors=tuple(errors),
        parsed=parsed if not errors else None,
        schema_id=schema_id,
    )


def _validate_against_schema(value: Any, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in properties:
                errors.extend(
                    _validate_against_schema(child, properties[key], path=f"{path}.{key}")
                )
            elif additional is False:
                errors.append(f"{path}: unexpected property '{key}'")
        return errors
    if expected_type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_against_schema(item, item_schema, path=f"{path}[{index}]")
                )
        return errors
    if expected_type == "string" and not isinstance(value, str):
        return [f"{path}: expected string"]
    if expected_type == "integer" and not isinstance(value, int):
        return [f"{path}: expected integer"]
    if expected_type == "number" and not isinstance(value, (int, float)):
        return [f"{path}: expected number"]
    if expected_type == "boolean" and not isinstance(value, bool):
        return [f"{path}: expected boolean"]
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        errors.append(f"{path}: below minimum {schema['minimum']}")
    if "maximum" in schema and isinstance(value, (int, float)) and value > schema["maximum"]:
        errors.append(f"{path}: above maximum {schema['maximum']}")
    return errors


def build_default_schema_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.lab.rubric.v1",
            version="1",
            title="Lab Rubric",
            description="Training rubric for structured-output Lab ability",
            json_schema={
                "$id": "yonder.lab.rubric.v1",
                "type": "object",
                "required": ["title", "score", "criteria", "pass"],
                "properties": {
                    "title": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "met", "note"],
                            "properties": {
                                "name": {"type": "string"},
                                "met": {"type": "boolean"},
                                "note": {"type": "string"},
                            },
                        },
                    },
                    "pass": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.siteops.issue_event.v1",
            version="1",
            title="Site Ops Issue Event",
            description="Single evidence-backed issue from SEL or health",
            json_schema={
                "$id": "yonder.siteops.issue_event.v1",
                "type": "object",
                "required": ["host_id", "severity", "summary", "evidence_refs"],
                "properties": {
                    "host_id": {"type": "string"},
                    "severity": {"type": "string"},
                    "summary": {"type": "string"},
                    "likely_fru": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "next_action": {"type": "string"},
                },
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.siteops.health_findings.v1",
            version="1",
            title="Site Ops Health Findings",
            description="Component findings table with honest unknown states",
            json_schema={
                "$id": "yonder.siteops.health_findings.v1",
                "type": "object",
                "required": ["host_id", "findings"],
                "properties": {
                    "host_id": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["component", "status", "note"],
                            "properties": {
                                "component": {"type": "string"},
                                "status": {"type": "string"},
                                "note": {"type": "string"},
                            },
                        },
                    },
                },
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.siteops.incident_intake.v1",
            version="1",
            title="Site Ops Incident Intake",
            description="Structured DC incident ticket fields",
            json_schema={
                "$id": "yonder.siteops.incident_intake.v1",
                "type": "object",
                "required": ["host_id", "summary", "severity", "evidence_refs", "next_action"],
                "properties": {
                    "host_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "severity": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "next_action": {"type": "string"},
                    "owner_role": {"type": "string"},
                },
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.siteops.rma_packet.v1",
            version="1",
            title="Site Ops RMA Packet",
            description="Structured RMA fields — no invented warranty dates",
            json_schema={
                "$id": "yonder.siteops.rma_packet.v1",
                "type": "object",
                "required": ["host_id", "part_number", "failure_summary", "evidence_refs"],
                "properties": {
                    "host_id": {"type": "string"},
                    "part_number": {"type": "string"},
                    "failure_summary": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "warranty_end": {"type": "string"},
                    "contact_note": {"type": "string"},
                },
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        SchemaRegistryEntry(
            schema_id="yonder.siteops.parts_ledger_entry.v1",
            version="1",
            title="Site Ops Parts Ledger Entry",
            description="Sandbox parts usage against an incident",
            json_schema={
                "$id": "yonder.siteops.parts_ledger_entry.v1",
                "type": "object",
                "required": ["part_number", "quantity", "incident_id"],
                "properties": {
                    "part_number": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "incident_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                "additionalProperties": False,
            },
        )
    )
    return registry
