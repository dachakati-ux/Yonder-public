"""Minimal MCP JSON-RPC over stdio (Content-Length framing).

Gateway-owned implementation so lab/offline runs do not invent tool results
and do not require a live network MCP host for unit tests. Compatible with
Cursor/Claude Desktop stdio MCP clients for tools/list + tools/call.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TextIO


class McpProtocolError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def tool_qualified_name(server: str, tool: str) -> str:
    return f"mcp.{server}.{tool}"


def parse_qualified_name(name: str) -> tuple[str, str] | None:
    parts = (name or "").split(".", 2)
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def write_message(stream: TextIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    encoded = body.encode("utf-8")
    stream.write(f"Content-Length: {len(encoded)}\r\n\r\n")
    stream.write(body)
    stream.flush()


def read_message(stream: TextIO, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Read one Content-Length framed JSON-RPC message from a text stream."""

    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == "":
            raise McpProtocolError("eof", "MCP stream closed while reading headers.")
        if line in {"\r\n", "\n"}:
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    if "content-length" not in headers:
        raise McpProtocolError("bad_frame", "Missing Content-Length header.")
    try:
        length = int(headers["content-length"])
    except ValueError as exc:
        raise McpProtocolError("bad_frame", "Invalid Content-Length.") from exc
    if length < 0 or length > 8_000_000:
        raise McpProtocolError("bad_frame", "Content-Length out of bounds.")
    body = stream.read(length)
    if len(body) != length:
        raise McpProtocolError("eof", "MCP stream closed while reading body.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise McpProtocolError("bad_json", "MCP body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise McpProtocolError("bad_json", "MCP body must be a JSON object.")
    return payload


@dataclass
class McpStdioSession:
    """Client session to a subprocess MCP server over stdio."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    _proc: subprocess.Popen[str] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1
    _initialized: bool = False

    def start(self) -> None:
        if self._proc is not None:
            return
        if not self.command:
            raise McpProtocolError("command_required", "stdio MCP server requires a command.")
        merged_env = {**os.environ, **(self.env or {})}
        try:
            self._proc = subprocess.Popen(
                [self.command, *list(self.args)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
                env=merged_env,
            )
        except FileNotFoundError as exc:
            raise McpProtocolError(
                "command_not_found",
                f"MCP command not found: {self.command}",
            ) from exc
        self._initialize()

    def is_alive(self) -> bool:
        """Not-yet-started or running = alive; a subprocess that exited = dead."""
        return self._proc is None or self._proc.poll() is None

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self._initialized = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

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
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
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
            raise McpProtocolError("initialize_failed", "MCP initialize returned non-object.")
        # notifications/initialized (no id)
        self._notify("notifications/initialized", {})
        self._initialized = True

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        proc = self._require_proc()
        assert proc.stdin is not None
        with self._lock:
            write_message(proc.stdin, {"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        proc = self._require_proc()
        assert proc.stdin is not None
        assert proc.stdout is not None
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            write_message(
                proc.stdin,
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
            )
            # Wall-clock watchdog: a server that sends a Content-Length header
            # then stalls would otherwise block the read forever. Killing the
            # process makes the blocked read return EOF and bounds the call.
            timed_out = {"fired": False}

            def _kill_on_timeout() -> None:
                timed_out["fired"] = True
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

            watchdog = threading.Timer(max(self.timeout_seconds, 1.0), _kill_on_timeout)
            watchdog.start()
            try:
                # Read until matching id (skip notifications).
                deadline_reads = 50
                for _ in range(deadline_reads):
                    if proc.poll() is not None:
                        if timed_out["fired"]:
                            raise McpProtocolError(
                                "timeout",
                                f"MCP server timed out after {self.timeout_seconds}s "
                                f"for {method}.",
                            )
                        err = ""
                        if proc.stderr:
                            err = proc.stderr.read()[-2000:]
                        raise McpProtocolError(
                            "process_exited",
                            "MCP server process exited.",
                            details={"returncode": proc.returncode, "stderr": err},
                        )
                    try:
                        msg = read_message(proc.stdout)
                    except McpProtocolError:
                        if timed_out["fired"]:
                            raise McpProtocolError(
                                "timeout",
                                f"MCP server timed out after {self.timeout_seconds}s "
                                f"for {method}.",
                            ) from None
                        raise
                    if msg.get("id") != req_id:
                        continue
                    if "error" in msg:
                        err = msg.get("error") or {}
                        raise McpProtocolError(
                            "remote_error",
                            str(err.get("message") or "MCP remote error"),
                            details={"error": err},
                        )
                    return msg.get("result")
                raise McpProtocolError("timeout", f"No MCP response for {method}.")
            finally:
                watchdog.cancel()

    def _require_proc(self) -> subprocess.Popen[str]:
        if self._proc is None:
            raise McpProtocolError("not_started", "MCP stdio session is not started.")
        return self._proc


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class McpStdioServer:
    """Serve MCP tools over stdio for Cursor / Claude Desktop."""

    name: str = "yonder"
    tools: dict[str, tuple[dict[str, Any], ToolHandler]] = field(default_factory=dict)

    def register(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        self.tools[name] = (
            {
                "name": name,
                "description": description,
                "inputSchema": parameters
                if parameters
                else {"type": "object", "properties": {}},
            },
            handler,
        )

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        while True:
            try:
                msg = read_message(stdin)
            except McpProtocolError as exc:
                if exc.code == "eof":
                    return
                write_message(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": exc.message},
                    },
                )
                continue
            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            if method is None:
                continue
            if msg_id is None:
                # notification — ignore
                continue
            try:
                result = self._dispatch(str(method), params)
                write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": result})
            except McpProtocolError as exc:
                write_message(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32000,
                            "message": exc.message,
                            "data": {"code": exc.code, **exc.details},
                        },
                    },
                )
            except Exception as exc:  # noqa: BLE001
                write_message(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32603, "message": str(exc)},
                    },
                )

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": "0.1.0"},
            }
        if method == "tools/list":
            return {"tools": [meta for meta, _ in self.tools.values()]}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name not in self.tools:
                raise McpProtocolError("tool_not_found", f"Unknown tool: {name}")
            _meta, handler = self.tools[name]
            payload = handler(arguments)
            # MCP tools/call result shape
            text = payload.get("content")
            if not isinstance(text, str):
                text = json.dumps(payload, ensure_ascii=False)
            is_error = bool(payload.get("is_error"))
            return {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            }
        if method == "ping":
            return {}
        raise McpProtocolError("method_not_found", f"Unsupported MCP method: {method}")
