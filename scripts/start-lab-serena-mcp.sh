#!/usr/bin/env bash
# Start Serena MCP (streamable-HTTP) on loopback for Yonder Gateway client.
#
# LAB ONLY — binds 127.0.0.1 only. Gateway allowlists http://127.0.0.1:8900.
# See docs/mcp.md and docs/coder-live-canary.md.
#
# Preferred (air-gapped lab): offline site-packages tree at
#   /opt/yonder/serena/site-packages
# staged from a networked host (see docs/mcp.md). Override with SERENA_SITE.
#
# Fallback (online hosts): uvx --from serena-agent …
#
# Usage:
#   ./scripts/start-lab-serena-mcp.sh
#   ./scripts/start-lab-serena-mcp.sh --foreground
#   SERENA_PROJECT=/opt/yonder/src ./scripts/start-lab-serena-mcp.sh
set -euo pipefail

while [[ $# -gt 0 ]]; do
  case "$1" in
    --foreground|-f) shift ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      printf 'Unknown arg: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

HOST="${SERENA_HOST:-127.0.0.1}"
PORT="${SERENA_PORT:-8900}"
PROJECT="${SERENA_PROJECT:-/opt/yonder/repo}"
CONTEXT="${SERENA_CONTEXT:-ide-assistant}"
SITE="${SERENA_SITE:-/opt/yonder/serena/site-packages}"
PY="${SERENA_PYTHON:-/opt/yonder/venv/bin/python}"

log() { printf '[serena-mcp] %s\n' "$*"; }
die() { printf '[serena-mcp] ERROR: %s\n' "$*" >&2; exit 1; }

case "${HOST}" in
  127.0.0.1|localhost|::1) ;;
  *)
    die "Refusing non-loopback SERENA_HOST=${HOST}. Use 127.0.0.1."
    ;;
esac

[[ -d "${PROJECT}" ]] || die "SERENA_PROJECT not a directory: ${PROJECT}"

SERENA_ARGS=(
  start-mcp-server
  --transport streamable-http
  --host "${HOST}"
  --port "${PORT}"
  --project "${PROJECT}"
  --context "${CONTEXT}"
)

if [[ -d "${SITE}/serena" ]]; then
  [[ -x "${PY}" ]] || die "SERENA_PYTHON not executable: ${PY}"
  export PYTHONPATH="${SITE}${PYTHONPATH:+:${PYTHONPATH}}"
  log "mode=offline site=${SITE} python=${PY}"
  log "host=${HOST} port=${PORT} project=${PROJECT} context=${CONTEXT}"
  # site-packages target installs lack a usable console_scripts shebang; call Click entry directly.
  exec "${PY}" - <<PY
import sys
from serena.cli import top_level
sys.argv = [
    "serena",
    "start-mcp-server",
    "--transport", "streamable-http",
    "--host", "${HOST}",
    "--port", "${PORT}",
    "--project", "${PROJECT}",
    "--context", "${CONTEXT}",
    "--enable-web-dashboard", "false",
    "--open-web-dashboard", "false",
]
raise SystemExit(top_level())
PY
fi

# Online fallback: uvx
UVX_BIN="${UVX:-}"
if [[ -z "${UVX_BIN}" ]]; then
  if command -v uvx >/dev/null 2>&1; then
    UVX_BIN="$(command -v uvx)"
  elif [[ -x ${HOME}/.local/bin/uvx ]]; then
    UVX_BIN=${HOME}/.local/bin/uvx
  elif [[ -x /var/lib/yonder/.local/bin/uvx ]]; then
    UVX_BIN=/var/lib/yonder/.local/bin/uvx
  elif command -v uv >/dev/null 2>&1; then
    _uv="$(command -v uv)"
    _sib="$(dirname "${_uv}")/uvx"
    if [[ -x "${_sib}" ]]; then
      UVX_BIN="${_sib}"
    fi
  fi
fi

[[ -n "${UVX_BIN}" ]] || die "No offline Serena site at ${SITE} and uvx not found. Stage site-packages (docs/mcp.md) or install uvx."

log "mode=uvx bin=${UVX_BIN}"
log "host=${HOST} port=${PORT} project=${PROJECT} context=${CONTEXT}"
exec "${UVX_BIN}" --from serena-agent serena "${SERENA_ARGS[@]}"
