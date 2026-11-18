#!/usr/bin/env bash
# Point stable lab aliases at current READY deployments without inventing
# readiness. Prefer explicit DEPLOYMENT_ID_* overrides; otherwise pick the first
# ready deployment matching MODEL_ID + optional WORKER_ID suffix.
#
# Examples:
#   GATEWAY_URL=http://127.0.0.1:8080 ./scripts/lab-set-stable-aliases.sh
#   NEMOTRON_AI3_ID=yonder-nemotron-3-nano-4b-wai3-g0 \
#   NEMOTRON_AI7_ID=yonder-nemotron-3-nano-4b-wai7-g0 \
#   ./scripts/lab-set-stable-aliases.sh
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"

die() { printf '[lab-aliases] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[lab-aliases] %s\n' "$*"; }

command -v curl >/dev/null || die "curl required"
command -v python3 >/dev/null || die "python3 required"

DEPLOYMENTS_JSON="$(curl -fsS "${GATEWAY_URL}/api/admin/deployments")"

pick_ready() {
  local model_id="$1"
  local worker_id="${2:-}"
  printf '%s' "${DEPLOYMENTS_JSON}" | python3 -c '
import json, sys
model_id = sys.argv[1]
worker_id = sys.argv[2] or None
body = json.load(sys.stdin)
deps = body.get("deployments") or []
for dep in deps:
    if dep.get("model_definition_id") != model_id:
        continue
    if worker_id and dep.get("worker_id") != worker_id:
        continue
    if dep.get("status") != "ready":
        continue
    print(dep.get("id") or "")
    raise SystemExit(0)
' "${model_id}" "${worker_id}"
}

set_alias() {
  local alias="$1"
  local deployment_id="$2"
  [[ -n "${deployment_id}" ]] || {
    log "skip ${alias}: no ready deployment"
    return 0
  }
  log "PATCH ${alias} -> ${deployment_id}"
  curl -fsS -X PATCH "${GATEWAY_URL}/api/admin/routes/${alias}" \
    -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"deployment_id":sys.argv[1],"enabled":True}))' "${deployment_id}")" \
    >/dev/null
}

NEMOTRON_AI3="${NEMOTRON_AI3_ID:-$(pick_ready yonder-nemotron-3-nano-4b worker-ai3)}"
NEMOTRON_AI7="${NEMOTRON_AI7_ID:-$(pick_ready yonder-nemotron-3-nano-4b worker-ai7)}"
QWEN35="${QWEN35_ID:-$(pick_ready yonder-qwen35-9b)}"
CHAT="${CHAT_ID:-$(pick_ready yonder-qwen36-35b-a3b-q4)}"
CODER="${CODER_ID:-$(pick_ready yonder-qwen3-coder-next-q4)}"
EMBED4="${EMBED4_ID:-$(pick_ready yonder-qwen3-embedding-4b)}"
EMBED8="${EMBED8_ID:-$(pick_ready yonder-qwen3-embedding-8b)}"
RERANK="${RERANK_ID:-$(pick_ready yonder-qwen3-reranker-4b)}"

# Default Nemotron alias stays on ai3 when available (stable client name).
set_alias yonder-nemotron-4b "${NEMOTRON_AI3:-${NEMOTRON_AI7}}"
set_alias yonder-nemotron-4b-ai7 "${NEMOTRON_AI7}"
set_alias yonder-qwen35-9b "${QWEN35}"
set_alias yonder-chat "${CHAT}"
set_alias yonder-coder "${CODER}"
set_alias yonder-embed-4b "${EMBED4}"
set_alias yonder-embed-8b "${EMBED8}"
set_alias yonder-rerank-4b "${RERANK}"

log "current routes:"
curl -fsS "${GATEWAY_URL}/api/admin/routes" | python3 -c '
import json,sys
body=json.load(sys.stdin)
for route in body.get("aliases") or body.get("routes") or []:
    alias = route.get("alias") or route.get("id")
    dep = route.get("deployment_id")
    state = route.get("state")
    print("  %s: deployment=%s state=%s" % (alias, dep, state))
'
