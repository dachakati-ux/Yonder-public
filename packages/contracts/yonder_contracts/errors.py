"""Structured error contract for Yonder APIs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_LOADING = "MODEL_LOADING"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    QUEUE_TIMEOUT = "QUEUE_TIMEOUT"
    INFERENCE_TIMEOUT = "INFERENCE_TIMEOUT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    CONFLICTING_OPERATION = "CONFLICTING_OPERATION"
    GPU_PLACEMENT_CONFLICT = "GPU_PLACEMENT_CONFLICT"
    SECURITY_CONTEXT_UNAVAILABLE = "SECURITY_CONTEXT_UNAVAILABLE"
    PERMISSION_CONTEXT_REQUIRED = "PERMISSION_CONTEXT_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
