"""Central request-context middleware and FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from yonder_contracts.errors import ErrorBody, ErrorCode, ErrorResponse
from yonder_contracts.principal import RequestPrincipal


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign request IDs and resolve trusted principal once per request."""

    def __init__(self, app: Any, provider: Any, service_name: str) -> None:
        super().__init__(app)
        self.provider = provider
        self.service_name = service_name

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        yonder_request_id = f"req_{uuid.uuid4().hex[:16]}"
        upstream = request.headers.get("x-request-id")
        request.state.yonder_request_id = yonder_request_id
        request.state.upstream_request_id = upstream
        request.state.service_name = self.service_name

        # Operational probes, CORS preflight, and public OIDC handshake routes
        # do not require a pre-existing principal.
        if request.method == "OPTIONS" or request.url.path in {
            "/health",
            "/ready",
            "/version",
            "/metrics",
            "/v1/auth/login",
            "/v1/auth/callback",
            "/v1/auth/logout",
            "/v1/auth/providers",
        }:
            request.state.principal = None
            response = await call_next(request)
            response.headers["X-Yonder-Request-Id"] = yonder_request_id
            if upstream:
                response.headers["X-Request-Id"] = upstream
            return response

        try:
            principal = await self.provider.resolve(request)
        except Exception as exc:  # noqa: BLE001 — map provider failures closed
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                payload = exc.detail
                if not isinstance(payload, dict):
                    payload = ErrorResponse(
                        error=ErrorBody(
                            code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                            message=str(payload),
                            request_id=yonder_request_id,
                        )
                    ).model_dump()
                response = JSONResponse(status_code=exc.status_code, content=payload)
                response.headers["X-Yonder-Request-Id"] = yonder_request_id
                return response

            response = JSONResponse(
                status_code=401,
                content=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Failed to resolve trusted request context.",
                        request_id=yonder_request_id,
                        details={"reason": str(exc)},
                    )
                ).model_dump(),
            )
            response.headers["X-Yonder-Request-Id"] = yonder_request_id
            return response

        request.state.principal = principal
        response = await call_next(request)
        response.headers["X-Yonder-Request-Id"] = yonder_request_id
        if upstream:
            response.headers["X-Request-Id"] = upstream
        return response


def get_principal(request: Request) -> RequestPrincipal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise RuntimeError("Request principal missing; middleware misconfigured")
    return principal


def get_request_id(request: Request) -> str:
    return getattr(request.state, "yonder_request_id", "unknown")
