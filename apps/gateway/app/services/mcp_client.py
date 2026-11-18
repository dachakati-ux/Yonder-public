"""Gateway MCP client — connect to configured external MCP servers only."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from yonder_configuration.models import McpConfig, McpRemoteServerConfig

from app.services.mcp_protocol import (
    McpProtocolError,
    McpStdioSession,
    tool_qualified_name,
)
from app.services.secret_scan import redact_secrets


class McpSession(Protocol):
    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass
class McpHttpSession:
    """Minimal Streamable-HTTP-style MCP client (JSON-RPC POST)."""

    url: str
    timeout_seconds: float = 30.0
    _client: httpx.Client | None = None
    _next_id: int = 1
    _initialized: bool = False
    _session_id: str | None = None

    def start(self) -> None:
        if self._client is not None:
            return
        # follow_redirects stays False: a redirect could point off an
        # allowlisted host (the allowlist is only checked on the configured URL).
        self._client = httpx.Client(
            timeout=self.timeout_seconds, follow_redirects=False
        )
        self._initialize()

    def close(self) -> None:
        client = self._client
        self._client = None
        self._initialized = False
        if client is not None:
            client.close()

    def list_tools(self) -> list[dict[str, Any]]:
        self.start()
        result = self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        out: list[dict[str, Any]] = []
        for item in tools:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            out.append(
                {
                    "name": str(item["name"]),
                    "description": str(item.get("description") or ""),
                    "parameters": item.get("inputSchema")
                    if isinstance(item.get("inputSchema"), dict)
                    else {"type": "object", "properties": {}},
                }
            )
        return out

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise McpProtocolError("bad_result", "tools/call returned non-object result.")
        return result

    def _initialize(self) -> None:
        if self._initialized:
            return
        result = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "yonder-gateway", "version": "0.1.0"},
            },
        )
        if not isinstance(result, dict):
            raise McpProtocolError("initialize_failed", "MCP HTTP initialize returned non-object.")
        self._notify("notifications/initialized", {})
        self._initialized = True

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        client = self._require_client()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = client.post(
            self.url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": method, "params": params},
        )
        sid = response.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        client = self._require_client()
        req_id = self._next_id
        self._next_id += 1
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            response = client.post(
                self.url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
            )
        except httpx.HTTPError as exc:
            raise McpProtocolError("http_error", f"MCP HTTP request failed: {exc}") from exc
        sid = response.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if response.status_code >= 400:
            raise McpProtocolError(
                "http_status",
                f"MCP HTTP status {response.status_code}",
                details={"status_code": response.status_code, "body": response.text[:500]},
            )
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/event-stream" in content_type:
            payload = _parse_sse_jsonrpc(response.text, req_id)
        else:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise McpProtocolError("bad_json", "MCP HTTP body is not JSON.") from exc
        if not isinstance(payload, dict):
            raise McpProtocolError("bad_json", "MCP HTTP body must be a JSON object.")
        if payload.get("id") not in {None, req_id}:
            # Some servers omit id on errors; still surface error body.
            pass
        if "error" in payload:
            err = payload.get("error") or {}
            raise McpProtocolError(
                "remote_error",
                str(err.get("message") or "MCP remote error"),
                details={"error": err},
            )
        return payload.get("result")

    def _require_client(self) -> httpx.Client:
        if self._client is None:
            raise McpProtocolError("not_started", "MCP HTTP session is not started.")
        return self._client


def _parse_sse_jsonrpc(body: str, req_id: int) -> dict[str, Any]:
    """Extract the JSON-RPC response matching req_id from an SSE body."""

    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.strip() == "" and data_lines:
            chunk = "\n".join(data_lines)
            data_lines = []
            try:
                payload = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("id") == req_id:
                return payload
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    raise McpProtocolError("bad_sse", "No matching JSON-RPC message in SSE body.")


def url_allowed(url: str, allowlist: list[str]) -> bool:
    """Allow only URLs whose (scheme, host, port) matches an allowlist entry.

    Matching is done on the *parsed* URL, never on the raw string — a prefix
    startswith() check is bypassable via userinfo
    (``http://127.0.0.1:9999@evil.com`` starts with ``http://127.0.0.1:`` but
    resolves to evil.com) or a host suffix (``good.com`` vs ``good.com.evil``).
    Any URL carrying userinfo is refused outright.
    """
    cleaned = (url or "").strip()
    if not cleaned or not allowlist:
        return False
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    # Reject embedded credentials — the classic allowlist-bypass vector.
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    for entry in allowlist:
        allow = (entry or "").strip()
        if not allow:
            continue
        allow_parsed = urlparse(allow)
        if allow_parsed.scheme and allow_parsed.scheme != parsed.scheme:
            continue
        allow_host = (allow_parsed.hostname or "").lower()
        if not allow_host or allow_host != host:
            continue
        try:
            allow_port = allow_parsed.port
        except ValueError:
            continue
        # An allowlist entry without an explicit port permits any port on that
        # host; with a port, the ports must match exactly.
        if allow_port is not None and allow_port != port:
            continue
        return True
    return False


@dataclass
class ServerAvailability:
    name: str
    enabled: bool
    transport: str
    availability: str
    detail: str = ""
    tool_count: int | None = None


SessionFactory = Callable[[McpRemoteServerConfig], McpSession]


@dataclass
class McpClientManager:
    """Config-gated MCP client. Off by default; fail closed on deny/errors."""

    config: McpConfig
    audit: Callable[[dict[str, Any]], None] | None = None
    session_factory: SessionFactory | None = None
    _sessions: dict[str, McpSession] = field(default_factory=dict)
    _tool_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _availability: dict[str, ServerAvailability] = field(default_factory=dict)
    _lock: Any = field(default_factory=__import__("threading").Lock)

    def status(self) -> dict[str, Any]:
        servers = [self._probe_server(cfg) for cfg in self.config.servers]
        return {
            "enabled": bool(self.config.enabled),
            "server_enabled": bool(self.config.server_enabled),
            "server_bind": self.config.server_bind,
            "server_port": self.config.server_port,
            "bridge_into_tool_executor": bool(self.config.bridge_into_tool_executor),
            "http_url_allowlist": list(self.config.http_url_allowlist),
            "servers": [s.__dict__ for s in servers],
            "availability": "ready" if self.config.enabled else "not_configured",
        }

    def list_servers(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cfg in self.config.servers:
            out.append(
                {
                    "name": cfg.name,
                    "enabled": cfg.enabled,
                    "transport": cfg.transport,
                    "command": cfg.command if cfg.transport == "stdio" else "",
                    "args": list(cfg.args) if cfg.transport == "stdio" else [],
                    "url": cfg.url if cfg.transport == "http" else "",
                    # Never expose env secrets.
                    "has_env": bool(cfg.env),
                    "timeout_seconds": cfg.timeout_seconds,
                }
            )
        return out

    def list_tools(self, *, server: str | None = None) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                "tools": [],
                "availability": "not_configured",
                "detail": "mcp.enabled is false",
            }
        tools: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for cfg in self.config.servers:
            if server and cfg.name != server:
                continue
            if not cfg.enabled:
                continue
            try:
                discovered = self._list_server_tools(cfg)
                for tool in discovered:
                    tools.append(
                        {
                            "name": tool_qualified_name(cfg.name, tool["name"]),
                            "server": cfg.name,
                            "tool": tool["name"],
                            "description": tool.get("description") or "",
                            "parameters": tool.get("parameters")
                            or {"type": "object", "properties": {}},
                        }
                    )
            except McpClientError as exc:
                errors.append({"server": cfg.name, "code": exc.code, "message": exc.message})
        availability = "ready"
        if not tools and errors:
            availability = "collection_failed"
        elif not tools:
            availability = "not_ready"
        return {"tools": tools, "availability": availability, "errors": errors}

    def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        principal_subject: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise McpClientError("not_configured", "mcp.enabled is false")
        cfg = self._require_server(server)
        if not cfg.enabled:
            raise McpClientError("server_disabled", f"MCP server disabled: {server}")
        session = self._session_for(cfg)
        try:
            raw = session.call_tool(tool, arguments or {})
        except McpProtocolError as exc:
            self._evict(server)
            self._audit(
                "mcp.call.failed",
                server=server,
                tool=tool,
                principal_subject=principal_subject,
                request_id=request_id,
                result="error",
                payload={
                    "code": exc.code,
                    "message": _redact(exc.message),
                    "details": _redact_details(exc.details),
                },
            )
            raise McpClientError(
                exc.code, _redact(exc.message), details=_redact_details(exc.details)
            ) from exc
        except Exception as exc:  # noqa: BLE001
            self._evict(server)
            self._audit(
                "mcp.call.failed",
                server=server,
                tool=tool,
                principal_subject=principal_subject,
                request_id=request_id,
                result="error",
                payload={"code": "collection_failed", "message": _redact(str(exc))},
            )
            raise McpClientError("collection_failed", _redact(str(exc))) from exc

        # Content-level secret redaction on everything leaving the gateway —
        # external MCP servers routinely leak tokens in tool output/errors.
        content = _redact(_normalize_call_content(raw))
        is_error = bool(raw.get("isError") or raw.get("is_error"))
        self._audit(
            "mcp.call",
            server=server,
            tool=tool,
            principal_subject=principal_subject,
            request_id=request_id,
            result="error" if is_error else "ok",
            payload={"qualified_name": tool_qualified_name(server, tool)},
        )
        return {
            "server": server,
            "tool": tool,
            "name": tool_qualified_name(server, tool),
            "content": content,
            "is_error": is_error,
            "availability": "ready",
        }

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._tool_cache.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def _evict(self, name: str) -> None:
        """Drop a server's cached session + tools so the next call reconnects."""
        with self._lock:
            session = self._sessions.pop(name, None)
            self._tool_cache.pop(name, None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def bridge_handlers(
        self,
    ) -> list[tuple[str, str, dict[str, Any], Callable[[dict[str, Any]], str]]]:
        """Return (qualified_name, description, parameters, handler) for ToolExecutor.

        NOTE: bridged tools are registered on the shared ToolExecutor at
        startup, so consumers reach them without a per-call capability check.
        Audit rows are attributed to the synthetic ``mcp-bridge`` subject so
        calls are never anonymous. The **coder agent does not use this global
        bridge** — it has a dedicated per-run, allowlisted MCP path
        (``coder.mcp_tools`` in ``CoderRunOrchestrator._register_mcp_tools``)
        that attributes each call to the run's principal + id. Keep
        ``bridge_into_tool_executor`` false in any environment where untrusted
        model output drives the shared executor.
        """

        if not self.config.enabled or not self.config.bridge_into_tool_executor:
            return []
        listed = self.list_tools()
        handlers: list[tuple[str, str, dict[str, Any], Callable[[dict[str, Any]], str]]] = []
        for tool in listed.get("tools") or []:
            server = str(tool["server"])
            short = str(tool["tool"])
            qualified = str(tool["name"])

            def _make(s: str = server, t: str = short) -> Callable[[dict[str, Any]], str]:
                def handler(args: dict[str, Any]) -> str:
                    result = self.call(
                        server=s,
                        tool=t,
                        arguments=args,
                        principal_subject="mcp-bridge",
                    )
                    content = result.get("content")
                    if isinstance(content, str):
                        return content
                    return json.dumps(content)

                return handler

            handlers.append(
                (
                    qualified,
                    str(tool.get("description") or f"MCP tool {qualified}"),
                    tool.get("parameters") or {"type": "object", "properties": {}},
                    _make(),
                )
            )
        return handlers

    def _probe_server(self, cfg: McpRemoteServerConfig) -> ServerAvailability:
        if not self.config.enabled:
            return ServerAvailability(
                name=cfg.name,
                enabled=cfg.enabled,
                transport=cfg.transport,
                availability="not_configured",
                detail="mcp.enabled is false",
            )
        if not cfg.enabled:
            return ServerAvailability(
                name=cfg.name,
                enabled=False,
                transport=cfg.transport,
                availability="not_ready",
                detail="server disabled",
            )
        try:
            tools = self._list_server_tools(cfg)
            return ServerAvailability(
                name=cfg.name,
                enabled=True,
                transport=cfg.transport,
                availability="ready",
                tool_count=len(tools),
            )
        except McpClientError as exc:
            avail = "collection_failed"
            if exc.code in {
                "url_not_allowlisted",
                "transport_disabled",
                "command_required",
                "url_required",
            }:
                avail = "not_ready"
            return ServerAvailability(
                name=cfg.name,
                enabled=True,
                transport=cfg.transport,
                availability=avail,
                detail=exc.message,
            )

    def _list_server_tools(self, cfg: McpRemoteServerConfig) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._tool_cache.get(cfg.name)
        if cached is not None:
            return cached
        session = self._session_for(cfg)
        try:
            tools = session.list_tools()
        except McpProtocolError as exc:
            self._evict(cfg.name)
            raise McpClientError(exc.code, exc.message, details=exc.details) from exc
        except Exception as exc:  # noqa: BLE001
            self._evict(cfg.name)
            raise McpClientError("collection_failed", str(exc)) from exc
        with self._lock:
            self._tool_cache[cfg.name] = tools
        return tools

    def _session_for(self, cfg: McpRemoteServerConfig) -> McpSession:
        self._validate_server(cfg)
        with self._lock:
            existing = self._sessions.get(cfg.name)
            if existing is not None and _session_alive(existing):
                return existing
            if existing is not None:
                # Reap a dead session before reconnecting.
                self._sessions.pop(cfg.name, None)
                self._tool_cache.pop(cfg.name, None)
                try:
                    existing.close()
                except Exception:  # noqa: BLE001
                    pass
            if self.session_factory is not None:
                session = self.session_factory(cfg)
            elif cfg.transport == "stdio":
                session = McpStdioSession(
                    command=cfg.command,
                    args=list(cfg.args),
                    env=dict(cfg.env),
                    timeout_seconds=float(
                        cfg.timeout_seconds or self.config.call_timeout_seconds
                    ),
                )
            else:
                session = McpHttpSession(
                    url=cfg.url,
                    timeout_seconds=float(
                        cfg.timeout_seconds or self.config.call_timeout_seconds
                    ),
                )
            self._sessions[cfg.name] = session
            return session

    def _validate_server(self, cfg: McpRemoteServerConfig) -> None:
        if cfg.transport == "stdio":
            if not (cfg.command or "").strip():
                raise McpClientError(
                    "command_required", f"stdio server {cfg.name} requires command"
                )
            return
        if cfg.transport == "http":
            if not (cfg.url or "").strip():
                raise McpClientError("url_required", f"http server {cfg.name} requires url")
            if not url_allowed(cfg.url, list(self.config.http_url_allowlist)):
                raise McpClientError(
                    "url_not_allowlisted",
                    f"MCP URL not on http_url_allowlist: {cfg.url}",
                )
            return
        raise McpClientError("transport_disabled", f"Unsupported transport: {cfg.transport}")

    def _require_server(self, name: str) -> McpRemoteServerConfig:
        for cfg in self.config.servers:
            if cfg.name == name:
                return cfg
        raise McpClientError("unknown_server", f"Unknown MCP server: {name}")

    def _audit(
        self,
        event_type: str,
        *,
        server: str,
        tool: str,
        principal_subject: str | None,
        request_id: str | None,
        result: str,
        payload: dict[str, Any],
    ) -> None:
        if self.audit is None:
            return
        self.audit(
            {
                "event_type": event_type,
                "resource_type": "mcp",
                "resource_id": f"{server}.{tool}",
                "principal_subject": principal_subject,
                "request_id": request_id,
                "result": result,
                "payload": payload,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )


class McpClientError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _session_alive(session: McpSession) -> bool:
    """True unless a session reports itself dead (stdio proc exited)."""
    is_alive = getattr(session, "is_alive", None)
    if callable(is_alive):
        try:
            return bool(is_alive())
        except Exception:  # noqa: BLE001
            return False
    return True


def _redact(text: str) -> str:
    return redact_secrets(text).text


def _redact_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    try:
        return json.loads(_redact(json.dumps(details, ensure_ascii=False, default=str)))
    except (json.JSONDecodeError, TypeError):
        # Never let a redaction failure leak the raw details.
        return {"redaction": "unavailable"}


def _normalize_call_content(raw: dict[str, Any]) -> str:
    content = raw.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(raw, ensure_ascii=False)
