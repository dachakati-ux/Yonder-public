"""Yonder MCP server — IDE-facing stdio/HTTP wrapping Gateway HTTP only.

Console never speaks MCP. Cursor/Claude Desktop talk to this process; this
process calls Yonder Gateway over HTTP like the console does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from app.services.mcp_protocol import McpProtocolError, McpStdioServer

DEFAULT_TOOLS = (
    "yonder_gateway_status",
    "yonder_capacity_aliases",
    "yonder_list_platform_tools",
    "yonder_execute_platform_tool",
)


def gateway_base_url() -> str:
    return (os.environ.get("YONDER_GATEWAY_URL") or "http://127.0.0.1:8080").rstrip("/")


def _headers() -> dict[str, str]:
    # Development security mode accepts requests without corporate IAM headers
    # when the Gateway is configured for lab/dev. Optional passthrough headers
    # stay available for future trusted-context adapters.
    headers: dict[str, str] = {"Accept": "application/json"}
    subject = os.environ.get("YONDER_MCP_SUBJECT")
    if subject:
        headers["X-Yonder-Subject"] = subject
    return headers


def _get_json(path: str) -> dict[str, Any]:
    url = f"{gateway_base_url()}{path}"
    try:
        response = httpx.get(url, headers=_headers(), timeout=30.0)
    except httpx.HTTPError as exc:
        raise McpProtocolError("gateway_unreachable", f"Gateway request failed: {exc}") from exc
    if response.status_code >= 400:
        raise McpProtocolError(
            "gateway_error",
            f"Gateway returned {response.status_code} for {path}",
            details={"body": response.text[:800]},
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise McpProtocolError("bad_gateway_json", "Gateway response is not JSON.") from exc
    if not isinstance(payload, dict):
        raise McpProtocolError("bad_gateway_json", "Gateway response must be an object.")
    return payload


def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{gateway_base_url()}{path}"
    try:
        response = httpx.post(
            url,
            headers={**_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise McpProtocolError("gateway_unreachable", f"Gateway request failed: {exc}") from exc
    if response.status_code >= 400:
        raise McpProtocolError(
            "gateway_error",
            f"Gateway returned {response.status_code} for {path}",
            details={"body": response.text[:800]},
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise McpProtocolError("bad_gateway_json", "Gateway response is not JSON.") from exc
    if not isinstance(payload, dict):
        raise McpProtocolError("bad_gateway_json", "Gateway response must be an object.")
    return payload


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(payload, ensure_ascii=False), "is_error": False}


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "content": json.dumps({"error": code, "message": message, **extra}, ensure_ascii=False),
        "is_error": True,
    }


def tool_yonder_gateway_status(_args: dict[str, Any]) -> dict[str, Any]:
    health = _get_json("/health")
    ready = _get_json("/ready")
    return _ok(
        {
            "health": health,
            "ready": ready,
            "gateway_url": gateway_base_url(),
            "availability": "ready" if ready.get("ready") else "not_ready",
        }
    )


def tool_yonder_capacity_aliases(_args: dict[str, Any]) -> dict[str, Any]:
    # Academy capacity endpoint reports real ready|not_ready|not_reported.
    payload = _get_json("/v1/lab/capacity")
    return _ok(payload)


def tool_yonder_list_platform_tools(_args: dict[str, Any]) -> dict[str, Any]:
    payload = _get_json("/v1/platform/tools")
    return _ok(payload)


def tool_yonder_execute_platform_tool(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        return _err("invalid_arguments", "name is required")
    arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    payload = _post_json(
        "/v1/platform/tools/execute",
        {"name": name, "arguments": arguments},
    )
    # Preserve Gateway honesty about mock/availability — do not invent success.
    if payload.get("error"):
        return _err("gateway_error", "platform tool execute returned error", gateway=payload)
    return _ok(payload)


TOOL_HANDLERS = {
    "yonder_gateway_status": (
        "Report Yonder Gateway /health and /ready (honest ready|not_ready).",
        {"type": "object", "properties": {}},
        tool_yonder_gateway_status,
    ),
    "yonder_capacity_aliases": (
        "List teaching alias capacity from Gateway (ready|not_ready|not_reported).",
        {"type": "object", "properties": {}},
        tool_yonder_capacity_aliases,
    ),
    "yonder_list_platform_tools": (
        "List Gateway allowlisted platform tools (mock/training tools).",
        {"type": "object", "properties": {}},
        tool_yonder_list_platform_tools,
    ),
    "yonder_execute_platform_tool": (
        "Execute one Gateway allowlisted platform tool by name.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Platform tool name"},
                "arguments": {"type": "object"},
            },
            "required": ["name"],
        },
        tool_yonder_execute_platform_tool,
    ),
}


def build_server(*, enabled_tools: list[str] | None = None) -> McpStdioServer:
    allow = set(enabled_tools or DEFAULT_TOOLS)
    server = McpStdioServer(name="yonder")
    for name, (description, parameters, handler) in TOOL_HANDLERS.items():
        if name not in allow:
            continue

        def _wrap(h=handler):
            def run(arguments: dict[str, Any]) -> dict[str, Any]:
                try:
                    return h(arguments)
                except McpProtocolError as exc:
                    return _err(exc.code, exc.message, **exc.details)

            return run

        server.register(name, description=description, parameters=parameters, handler=_wrap())
    return server


def serve_stdio(enabled_tools: list[str] | None = None) -> None:
    server = build_server(enabled_tools=enabled_tools)
    server.serve(sys.stdin, sys.stdout)


def serve_http(
    *,
    bind: str = "127.0.0.1",
    port: int = 8095,
    enabled_tools: list[str] | None = None,
) -> None:
    """Loopback-only Streamable HTTP MCP endpoint (JSON-RPC POST)."""

    allow_non_localhost = os.environ.get(
        "YONDER_ALLOW_NON_LOCALHOST_BIND_IN_DEVELOPMENT", ""
    ).lower() in {
        "1",
        "true",
        "yes",
    }
    if bind not in {"127.0.0.1", "localhost", "::1"} and not allow_non_localhost:
        raise SystemExit(
            f"Refusing non-loopback MCP bind {bind!r}. Use 127.0.0.1 for IDE dogfood."
        )

    import uvicorn
    from fastapi import FastAPI

    mcp = build_server(enabled_tools=enabled_tools)
    app = FastAPI(title="yonder-mcp", docs_url=None, redoc_url=None)

    # Request/JSONResponse are imported at module scope so FastAPI can resolve
    # annotations under `from __future__ import annotations`.
    @app.post("/mcp")
    async def mcp_endpoint(http_request: Request) -> JSONResponse:
        payload = await http_request.json()
        if not isinstance(payload, dict):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Invalid JSON"},
                },
                status_code=400,
            )
        msg_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if method is None:
            return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        if msg_id is None:
            # notification
            return JSONResponse({"jsonrpc": "2.0", "result": {}})
        try:
            result = mcp._dispatch(str(method), params)
            return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})
        except McpProtocolError as exc:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": exc.message,
                        "data": {"code": exc.code, **exc.details},
                    },
                }
            )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "yonder-mcp"}

    uvicorn.run(app, host=bind, port=port, log_level="info")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Yonder MCP server (Gateway HTTP only)")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP on loopback instead of stdio",
    )
    parser.add_argument("--bind", default=os.environ.get("YONDER_MCP_BIND", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("YONDER_MCP_PORT", "8095")),
    )
    parser.add_argument(
        "--tools",
        default="",
        help="Comma-separated tool allowlist (default: read-only Yonder tools)",
    )
    args = parser.parse_args(argv)
    tools = [p.strip() for p in args.tools.split(",") if p.strip()] or list(DEFAULT_TOOLS)
    if args.http or os.environ.get("YONDER_MCP_HTTP", "").lower() in {"1", "true", "yes"}:
        serve_http(bind=args.bind, port=args.port, enabled_tools=tools)
    else:
        serve_stdio(enabled_tools=tools)


if __name__ == "__main__":
    main()
