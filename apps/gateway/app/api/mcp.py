"""Gateway MCP APIs — status, servers, tools, call (Gateway-mediated only)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.mcp_client import McpClientError

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


class McpCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _manager(request: Request):
    manager = getattr(request.app.state, "mcp_client", None)
    if manager is None:
        return None
    return manager


def _can_list(principal) -> bool:
    return (
        principal.has_capability("mcp.use")
        or principal.has_capability("tools.use")
        or principal.has_capability("lab.use")
    )


def _can_call(principal) -> bool:
    return principal.has_capability("mcp.use") or principal.has_capability("tools.use")


@router.get("/status")
async def mcp_status(request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not _can_list(principal):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks mcp.use / tools.use / lab.use capability.",
            request_id,
        )
    manager = _manager(request)
    if manager is None:
        return {
            "enabled": False,
            "server_enabled": False,
            "servers": [],
            "availability": "not_configured",
            "detail": "MCP client not initialized",
        }
    # status() probes every server (blocking network/subprocess I/O) — keep it
    # off the event loop so a slow MCP server can't stall the whole gateway.
    return await asyncio.to_thread(manager.status)


@router.get("/servers")
async def mcp_servers(request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not _can_list(principal):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks mcp.use / tools.use / lab.use capability.",
            request_id,
        )
    manager = _manager(request)
    if manager is None:
        return {"servers": [], "availability": "not_configured"}
    return {
        "servers": manager.list_servers(),
        "availability": "ready" if manager.config.enabled else "not_configured",
    }


@router.get("/tools")
async def mcp_tools(request: Request, server: str | None = None) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not _can_list(principal):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks mcp.use / tools.use / lab.use capability.",
            request_id,
        )
    manager = _manager(request)
    if manager is None:
        return {"tools": [], "availability": "not_configured"}
    return await asyncio.to_thread(manager.list_tools, server=server)


@router.post("/call")
async def mcp_call(body: McpCallRequest, request: Request) -> dict[str, object]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    if not _can_call(principal):
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks mcp.use or tools.use capability.",
            request_id,
        )
    manager = _manager(request)
    if manager is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "MCP client not configured.",
            request_id,
            details={"availability": "not_configured"},
        )
    try:
        result = await asyncio.to_thread(
            manager.call,
            server=body.server,
            tool=body.tool,
            arguments=body.arguments,
            principal_subject=principal.subject,
            request_id=request_id,
        )
    except McpClientError as exc:
        code = ErrorCode.INVALID_REQUEST
        if exc.code in {
            "not_configured",
            "collection_failed",
            "http_error",
            "http_status",
            "process_exited",
            "timeout",
            "remote_error",
        }:
            code = ErrorCode.MODEL_UNAVAILABLE
        return error_response(
            code,
            exc.message,
            request_id,
            details={"code": exc.code, **exc.details},
        )
    return {
        "server": result["server"],
        "tool": result["tool"],
        "name": result["name"],
        "content": result["content"],
        "is_error": result["is_error"],
        "availability": result["availability"],
    }
