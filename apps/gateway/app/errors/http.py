"""Structured HTTP error helpers for the gateway."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from yonder_contracts.errors import ErrorBody, ErrorCode, ErrorResponse

# Map error codes to default HTTP statuses.
ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNKNOWN_MODEL: 404,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_LOADING: 503,
    ErrorCode.CAPACITY_EXCEEDED: 429,
    ErrorCode.QUEUE_TIMEOUT: 504,
    ErrorCode.INFERENCE_TIMEOUT: 504,
    ErrorCode.PROVIDER_FAILURE: 502,
    ErrorCode.WORKER_UNAVAILABLE: 503,
    ErrorCode.CONFLICTING_OPERATION: 409,
    ErrorCode.GPU_PLACEMENT_CONFLICT: 409,
    ErrorCode.SECURITY_CONTEXT_UNAVAILABLE: 401,
    ErrorCode.PERMISSION_CONTEXT_REQUIRED: 403,
    ErrorCode.INTERNAL_ERROR: 500,
}


def error_payload(
    code: ErrorCode,
    message: str,
    request_id: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details or {},
        )
    ).model_dump()


def raise_error(
    code: ErrorCode,
    message: str,
    request_id: str,
    *,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
) -> None:
    raise HTTPException(
        status_code=status_code or ERROR_STATUS[code],
        detail=error_payload(code, message, request_id, details=details),
    )


def error_response(
    code: ErrorCode,
    message: str,
    request_id: str,
    *,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code or ERROR_STATUS[code],
        content=error_payload(code, message, request_id, details=details),
        headers={"X-Yonder-Request-Id": request_id},
    )
