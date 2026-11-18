# MCP (Model Context Protocol) — Gateway-mediated

Yonder supports MCP in **both directions**, always through the Gateway:

1. **MCP server (`yonder-mcp`)** — Cursor / Claude Desktop talk MCP to a local
   process; that process calls Gateway HTTP only (same trust boundary as Console).
2. **MCP client (`/v1/mcp/*`)** — Gateway may call **named, allowlisted** external
   MCP servers so Chat/Coder/ToolExecutor can use those tools.

The Console **never** speaks MCP wire protocol.

## Lab status (ai1)

`lab-multinode` and `development` enable MCP with Serena on loopback:

| Surface | Endpoint | Unit / process |
| --- | --- | --- |
| Gateway client | `http://127.0.0.1:8080/v1/mcp/*` | `yonder-gateway` |
| IDE MCP HTTP | `http://127.0.0.1:8095/mcp` | `yonder-mcp.service` |
| Serena MCP | `http://127.0.0.1:8900/mcp` | `yonder-serena-mcp.service` |

`bridge_into_tool_executor` stays **false**. Coder may call only the allowlisted
Serena symbol tools via `coder.mcp_tools` (see env YAML).

### Tool catalog (what we offer today)

| Surface | Tools | Notes |
| --- | --- | --- |
| IDE `yonder-mcp` | `yonder_gateway_status`, `yonder_capacity_aliases`, `yonder_list_platform_tools`, `yonder_execute_platform_tool` | Gateway HTTP wrappers only |
| Platform tools (via execute) | `echo`, `lookup_weather`, `yonder_fixture_get_ticket`, `yonder_fixture_get_runbook` | Training/mock |
| Serena (Gateway client) | ~21 discovered (`mcp.serena.*`); coder allowlist: `get_symbols_overview`, `find_symbol`, `find_referencing_symbols` | Code intelligence |
| Coder-native (not MCP) | `coder_docs_search`, Forgejo PR delivery | Separate from MCP wire protocol |

## Defaults (fail closed)

| Setting | Default |
| --- | --- |
| `mcp.enabled` | `false` (client off) |
| `mcp.server_enabled` | `false` (docs/status flag; IDE uses `yonder-mcp` process) |
| `mcp.bridge_into_tool_executor` | `false` |
| `mcp.servers` | `[]` |
| `mcp.http_url_allowlist` | `[]` |

Schema defaults remain off. Lab/dev env files turn the client on with an
explicit Serena allowlist. `server_enabled` does **not** spawn `yonder-mcp` —
use the systemd unit (or run the binary by hand).

## Capability

* List/status: `mcp.use`, `tools.use`, or `lab.use`
* Call / execute: `mcp.use` or `tools.use`

## Gateway client APIs

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/mcp/status` | enabled flags + per-server availability |
| GET | `/v1/mcp/servers` | configured servers (no secrets / env values) |
| GET | `/v1/mcp/tools` | aggregated tools as `mcp.<server>.<tool>` |
| POST | `/v1/mcp/call` | `{server, tool, arguments}` → content |

Availability values are honest: `ready`, `not_configured`, `not_ready`,
`collection_failed`. Tool results are never invented on connection failure.

### Config sketch (client + Serena)

```yaml
mcp:
  enabled: true
  server_enabled: true
  server_bind: 127.0.0.1
  server_port: 8095
  bridge_into_tool_executor: false
  http_url_allowlist:
    - http://127.0.0.1:8900
  servers:
    - name: serena
      transport: http
      url: http://127.0.0.1:8900/mcp
      enabled: true
coder:
  mcp_tools:
    - mcp.serena.get_symbols_overview
    - mcp.serena.find_symbol
    - mcp.serena.find_referencing_symbols
```

HTTP URLs are matched on the **parsed** `(scheme, host, port)`, never as a raw
string prefix — an allowlist entry with a port (`http://127.0.0.1:8900`) permits
only that port, and one without a port (`http://127.0.0.1:`) permits any port on
that exact host. URLs carrying userinfo (`user:pass@host`) are refused outright,
and redirects are not followed, so the allowlist cannot be bypassed by
`http://127.0.0.1:9999@evil.com` or a host suffix like `good.com.evil.com`.
Unknown servers, disabled transports, and allowlist misses fail closed.

Everything an external MCP server returns — tool output, error messages, and
subprocess stderr — is run through the same content-level secret scanner as the
coder jail (`secret_scan.redact_secrets`) before it reaches the caller or the
audit log; scanner failure fails closed. Dead sessions are reaped and
reconnected on the next call, stdio calls are bounded by a wall-clock watchdog
(a stalled server is killed rather than hanging the gateway), and all blocking
MCP I/O is offloaded off the async event loop.

When `bridge_into_tool_executor: true`, discovered tools are registered on the
Gateway `ToolExecutor` under `mcp.<server>.<tool>` (still Gateway-mediated).

## IDE MCP server (`yonder-mcp`)

Read-only / allowlisted tools that wrap Gateway HTTP:

| Tool | Gateway surface |
| --- | --- |
| `yonder_gateway_status` | `/health` + `/ready` |
| `yonder_capacity_aliases` | `/v1/lab/capacity` |
| `yonder_list_platform_tools` | `/v1/platform/tools` |
| `yonder_execute_platform_tool` | `/v1/platform/tools/execute` |

No coder write, no shell, no Forgejo via MCP in this slice.

### Stdio (Cursor / Claude Desktop)

```bash
export YONDER_GATEWAY_URL=http://127.0.0.1:8080
# From a venv that has the gateway package installed:
yonder-mcp
```

Example Cursor MCP settings snippet:

```json
{
  "mcpServers": {
    "yonder": {
      "command": "/path/to/yonder/.venv/bin/yonder-mcp",
      "env": {
        "YONDER_GATEWAY_URL": "http://127.0.0.1:8080"
      }
    }
  }
}
```

### Loopback HTTP (lab unit)

```bash
# systemd: yonder-mcp.service
yonder-mcp --http --bind 127.0.0.1 --port 8095
```

Non-loopback binds are refused. Do not put MCP on the public console origin.

Units: [`packaging/offline/systemd/yonder-mcp.service`](../packaging/offline/systemd/yonder-mcp.service),
[`packaging/offline/systemd/yonder-serena-mcp.service`](../packaging/offline/systemd/yonder-serena-mcp.service).

## Serena on air-gapped lab hosts

ai1 cannot reach PyPI. Stage offline artifacts on a networked host, then install:

* `/opt/yonder/serena/site-packages` — `serena-agent` + deps (Linux x86_64)
* `/opt/yonder/serena/pyright-site` — `pyright==1.1.403` (for LSP)
* `/opt/yonder/serena/bin/{node,uv,uvx}` — Node + uv; `uvx` may wrap pyright to
  `node …/langserver.index.js --stdio` when PyPI is unavailable
* Launcher: [`scripts/start-lab-serena-mcp.sh`](../scripts/start-lab-serena-mcp.sh)
  (prefers offline site; falls back to `uvx --from serena-agent` online)

Project root defaults to `/opt/yonder/repo`. Override with `/etc/yonder/serena-mcp.env`.

Verify:

```bash
curl -fsS http://127.0.0.1:8080/v1/mcp/status
curl -fsS http://127.0.0.1:8080/v1/mcp/tools
curl -fsS -H 'content-type: application/json' \
  -d '{"server":"serena","tool":"get_symbols_overview","arguments":{"relative_path":"apps/gateway/app/mcp_server.py"}}' \
  http://127.0.0.1:8080/v1/mcp/call
```

## Out of scope (this slice)

* MCP resources/prompts beyond tools
* Coder write/push or arbitrary shell via MCP
* OAuth to remote MCP hosts
* Auto-discovering random MCP servers on the network
* Exposing MCP on non-loopback / console origin

## Related

* Architecture: [`docs/architecture.md`](./architecture.md)
* Operations: [`docs/operations.md`](./operations.md)
* Coder canary (Serena sketch): [`docs/coder-live-canary.md`](./coder-live-canary.md)
* Platform tools: Gateway `/v1/platform/tools`
