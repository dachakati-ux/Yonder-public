"""Admission control for chat requests."""

from __future__ import annotations

from dataclasses import dataclass

from yonder_configuration.models import AdmissionConfig
from yonder_contracts.errors import ErrorCode

from app.routing.models import DeploymentRecord


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    code: ErrorCode | None = None
    message: str | None = None
    details: dict[str, str] | None = None


def estimate_prompt_chars(messages: list[object]) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            total += len(content)
    return total


def admit_request(
    *,
    deployment: DeploymentRecord,
    admission: AdmissionConfig,
    prompt_chars: int,
    max_tokens: int | None,
) -> AdmissionDecision:
    """Validate request size limits.

    Concurrency and queue bounds are enforced by DeploymentAdmissionGate.
    """

    if prompt_chars > admission.max_prompt_chars:
        return AdmissionDecision(
            allowed=False,
            code=ErrorCode.INVALID_REQUEST,
            message="Prompt exceeds maximum allowed size.",
            details={
                "prompt_chars": str(prompt_chars),
                "max_prompt_chars": str(admission.max_prompt_chars),
            },
        )

    if max_tokens is not None and max_tokens > admission.max_output_tokens:
        return AdmissionDecision(
            allowed=False,
            code=ErrorCode.INVALID_REQUEST,
            message="max_tokens exceeds configured limit.",
            details={
                "max_tokens": str(max_tokens),
                "limit": str(admission.max_output_tokens),
            },
        )

    return AdmissionDecision(allowed=True)
