"""MCP client + server tests (no live network MCP hosts)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.mcp_server import build_server, tool_yonder_gateway_status
from app.services.mcp_client import McpClientError, McpClientManager, url_allowed
from app.services.mcp_protocol import (
    McpProtocolError,
    McpStdioServer,
    parse_qualified_name,
    tool_qualified_name,
)
from fastapi.testclient import TestClient
from yonder_configuration.models import McpConfig, McpRemoteServerConfig

ROOT = Path(__file__).resolve().parents[3]


class FakeSession:
    def __init__(self, tools: list[dict[str, Any]] | None = None, *, fail: str | None = None):
        self.tools = tools or [
            {
                "name": "echo",
                "description": "Echo",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ]
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        if self.fail == "list":
            raise McpProtocolError("collection_failed", "boom")
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.fail == "call":
            raise McpProtocolError("remote_error", "call failed")
        self.calls.append((name, dict(arguments or {})))
        return {
            "content": [{"type": "text", "text": json.dumps({"echo": arguments})}],
            "isError": False,
        }

    def close(self) -> None:
        return None


def test_tool_name_prefixing() -> None:
    assert tool_qualified_name("docs", "search") == "mcp.docs.search"
    assert parse_qualified_name("mcp.docs.search") == ("docs", "search")
    assert parse_qualified_name("docs.search") is None


def test_url_allowlist_denial() -> None:
    assert not url_allowed("http://evil.example/mcp", ["http://127.0.0.1:"])
    assert url_allowed("http://127.0.0.1:9000/mcp", ["http://127.0.0.1:"])
    assert not url_allowed("ftp://127.0.0.1/mcp", ["ftp://127.0.0.1"])
    assert not url_allowed("http://127.0.0.1:9000/mcp", [])


def test_url_allowlist_rejects_userinfo_bypass() -> None:
    """The classic bypass: userinfo makes startswith() pass but host is evil."""
    assert not url_allowed(
        "http://127.0.0.1:9999@evil.com/mcp", ["http://127.0.0.1:"]
    )
    assert not url_allowed("http://user:pw@127.0.0.1:9000/mcp", ["http://127.0.0.1:"])


def test_url_allowlist_rejects_host_suffix_bypass() -> None:
    assert not url_allowed("http://good.com.evil.com/mcp", ["http://good.com"])
    assert url_allowed("http://good.com/mcp", ["http://good.com"])


def test_url_allowlist_port_scoping() -> None:
    # Entry with an explicit port only allows that port.
    assert url_allowed("http://127.0.0.1:3100/mcp", ["http://127.0.0.1:3100"])
    assert not url_allowed("http://127.0.0.1:3999/mcp", ["http://127.0.0.1:3100"])
    # Scheme must match when the entry specifies one.
    assert not url_allowed("http://127.0.0.1:3100/mcp", ["https://127.0.0.1:3100"])


def test_status_when_disabled() -> None:
    manager = McpClientManager(
        McpConfig(
            enabled=False,
            servers=[
                McpRemoteServerConfig(name="demo", transport="stdio", command="false"),
            ],
        )
    )
    status = manager.status()
    assert status["availability"] == "not_configured"
    assert status["enabled"] is False
    assert status["servers"][0]["availability"] == "not_configured"
    listed = manager.list_tools()
    assert listed["availability"] == "not_configured"
    assert listed["tools"] == []
    with pytest.raises(McpClientError) as exc:
        manager.call(server="demo", tool="echo", arguments={})
    assert exc.value.code == "not_configured"


def test_http_url_denied_before_connect() -> None:
    manager = McpClientManager(
        McpConfig(
            enabled=True,
            http_url_allowlist=["http://127.0.0.1:"],
            servers=[
                McpRemoteServerConfig(
                    name="remote",
                    transport="http",
                    url="https://evil.example/mcp",
                ),
            ],
        )
    )
    listed = manager.list_tools()
    assert listed["availability"] == "collection_failed"
    assert any(e["code"] == "url_not_allowlisted" for e in listed["errors"])


def test_list_and_call_with_fake_session() -> None:
    events: list[dict[str, Any]] = []
    fake = FakeSession()

    def factory(cfg: McpRemoteServerConfig) -> FakeSession:
        assert cfg.name == "demo"
        return fake

    manager = McpClientManager(
        McpConfig(
            enabled=True,
            servers=[McpRemoteServerConfig(name="demo", transport="stdio", command="true")],
        ),
        audit=lambda e: events.append(e),
        session_factory=factory,
    )
    listed = manager.list_tools()
    assert listed["availability"] == "ready"
    assert listed["tools"][0]["name"] == "mcp.demo.echo"
    result = manager.call(server="demo", tool="echo", arguments={"text": "hi"}, request_id="r1")
    assert result["name"] == "mcp.demo.echo"
    assert result["is_error"] is False
    assert "hi" in result["content"]
    assert any(e["event_type"] == "mcp.call" for e in events)


def test_bridge_handlers_prefix() -> None:
    fake = FakeSession()
    manager = McpClientManager(
        McpConfig(
            enabled=True,
            bridge_into_tool_executor=True,
            servers=[McpRemoteServerConfig(name="demo", transport="stdio", command="true")],
        ),
        session_factory=lambda _cfg: fake,
    )
    handlers = manager.bridge_handlers()
    assert len(handlers) == 1
    name, _desc, _params, handler = handlers[0]
    assert name == "mcp.demo.echo"
    content = handler({"text": "x"})
    assert "x" in content


def test_stdio_server_tools_list_and_call() -> None:
    server = McpStdioServer(name="test")

    def ping(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": json.dumps({"pong": True, "args": args}), "is_error": False}

    server.register(
        "ping",
        description="Ping",
        parameters={"type": "object", "properties": {}},
        handler=ping,
    )
    listed = server._dispatch("tools/list", {})
    assert listed["tools"][0]["name"] == "ping"
    called = server._dispatch("tools/call", {"name": "ping", "arguments": {}})
    assert called["isError"] is False
    assert "pong" in called["content"][0]["text"]


def test_yonder_mcp_gateway_status_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(path: str) -> dict[str, Any]:
        if path == "/health":
            return {"status": "ok", "service": "yonder-gateway"}
        if path == "/ready":
            return {"ready": True, "service": "yonder-gateway"}
        raise AssertionError(path)

    monkeypatch.setattr("app.mcp_server._get_json", fake_get)
    result = tool_yonder_gateway_status({})
    assert result["is_error"] is False
    payload = json.loads(result["content"])
    assert payload["availability"] == "ready"


def test_yonder_mcp_server_registers_default_tools() -> None:
    server = build_server()
    names = set(server.tools)
    assert "yonder_gateway_status" in names
    assert "yonder_execute_platform_tool" in names
    assert "yonder_shell" not in names


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    # Exercise the default-disabled route independent of the development
    # profile, which enables a local Serena MCP server for developers.
    monkeypatch.setenv("YONDER_MCP__ENABLED", "false")
    from app.main import create_app

    return TestClient(create_app())


def test_mcp_api_disabled_by_default(client: TestClient) -> None:
    status = client.get("/v1/mcp/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert body["availability"] == "not_configured"

    tools = client.get("/v1/mcp/tools")
    assert tools.status_code == 200
    assert tools.json()["availability"] == "not_configured"

    call = client.post("/v1/mcp/call", json={"server": "x", "tool": "y", "arguments": {}})
    assert call.status_code in {400, 503, 404}
    assert "error" in call.json()


def test_mcp_api_with_injected_manager(client: TestClient) -> None:
    fake = FakeSession()
    manager = McpClientManager(
        McpConfig(
            enabled=True,
            servers=[McpRemoteServerConfig(name="demo", transport="stdio", command="true")],
        ),
        session_factory=lambda _cfg: fake,
        audit=lambda e: None,
    )
    client.app.state.mcp_client = manager
    tools = client.get("/v1/mcp/tools")
    assert tools.status_code == 200
    assert tools.json()["tools"][0]["name"] == "mcp.demo.echo"
    call = client.post(
        "/v1/mcp/call",
        json={"server": "demo", "tool": "echo", "arguments": {"text": "ok"}},
    )
    assert call.status_code == 200
    assert call.json()["availability"] == "ready"


def test_unknown_server_fail_closed() -> None:
    manager = McpClientManager(
        McpConfig(enabled=True, servers=[]),
        session_factory=lambda _cfg: FakeSession(),
    )
    with pytest.raises(McpClientError) as exc:
        manager.call(server="missing", tool="echo")
    assert exc.value.code == "unknown_server"


def test_sse_jsonrpc_parsing() -> None:
    from app.services.mcp_client import _parse_sse_jsonrpc

    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":7,"result":{"tools":[]}}\n'
        "\n"
    )
    payload = _parse_sse_jsonrpc(body, 7)
    assert payload["id"] == 7
    assert payload["result"]["tools"] == []
    with pytest.raises(McpProtocolError):
        _parse_sse_jsonrpc("data: not-json\n\n", 7)


def test_mcp_output_is_secret_scanned() -> None:
    """Tokens in external MCP tool output must be redacted before returning."""

    class LeakySession:
        def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "leak", "description": "", "parameters": {}}]

        def call_tool(self, name: str, arguments=None) -> dict[str, Any]:
            return {
                "content": [
                    {"type": "text", "text": "token ghp_" + "a" * 36 + " done"}
                ],
                "isError": False,
            }

        def close(self) -> None:
            return None

    manager = McpClientManager(
        McpConfig(
            enabled=True,
            servers=[McpRemoteServerConfig(name="leaky", transport="stdio", command="x")],
        ),
        session_factory=lambda _cfg: LeakySession(),
    )
    result = manager.call(server="leaky", tool="leak")
    assert "ghp_" not in result["content"]
    assert "REDACTED:github_token" in result["content"]
    assert "raw" not in result  # unredacted raw payload is never returned


def test_dead_stdio_session_reconnects() -> None:
    """A stdio server whose process exited is reaped and reconnected."""
    from app.services.mcp_protocol import McpStdioSession

    # 'true' exits immediately, so the session is dead right after start.
    made: list[McpStdioSession] = []

    def factory(cfg: McpRemoteServerConfig) -> McpStdioSession:
        s = McpStdioSession(command="true", timeout_seconds=2.0)
        made.append(s)
        return s

    manager = McpClientManager(
        McpConfig(
            enabled=True,
            servers=[McpRemoteServerConfig(name="dead", transport="stdio", command="true")],
        ),
        session_factory=factory,
    )
    # First call fails (process exits before responding) and evicts the session.
    with pytest.raises(McpClientError):
        manager.call(server="dead", tool="whatever")
    # A second call must build a *new* session, not reuse the dead one.
    with pytest.raises(McpClientError):
        manager.call(server="dead", tool="whatever")
    assert len(made) == 2, "dead session was reused instead of reconnecting"


def test_manager_close_closes_sessions() -> None:
    closed = {"n": 0}

    class TrackSession(FakeSession):
        def close(self) -> None:
            closed["n"] += 1

    manager = McpClientManager(
        McpConfig(
            enabled=True,
            servers=[McpRemoteServerConfig(name="demo", transport="stdio", command="true")],
        ),
        session_factory=lambda _cfg: TrackSession(),
    )
    manager.list_tools()  # establishes a session
    manager.close()
    assert closed["n"] == 1
