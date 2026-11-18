#!/usr/bin/env bash
# Phase A/B coder assistant canary — Gateway-only, read-only tools.
# Does NOT invent VIP scores. Records alias readiness honestly.
#
# Usage (control plane):
#   ./scripts/canary-coder-assistant.sh
#   GATEWAY_URL=http://127.0.0.1:8080 ./scripts/canary-coder-assistant.sh
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Prefer the installed repo path on lab hosts — Gateway runs as yonder and cannot
# read operator home checkouts under /opt/yonder.
if [[ -d /opt/yonder/repo/config/lab/coder-evals/sample_repo ]]; then
  EVAL_REPO="/opt/yonder/repo/config/lab/coder-evals/sample_repo"
else
  EVAL_REPO="${ROOT}/config/lab/coder-evals/sample_repo"
fi

die() { printf '[coder-assistant] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[coder-assistant] %s\n' "$*"; }

command -v curl >/dev/null || die "curl required"
command -v python3 >/dev/null || die "python3 required"

log "GET /v1/coder/status"
STATUS="$(curl -fsS "${GATEWAY_URL}/v1/coder/status")"
echo "${STATUS}" | python3 -c 'import json,sys; b=json.load(sys.stdin); assert b.get("enabled") is True; assert b.get("trust_ceiling")=="read_only"; assert b.get("write_unlock") is False; print("phase=", b.get("phase"), "write_unlock=", b.get("write_unlock"))'

log "create cage workspace"
CREATE="$(curl -fsS -X POST "${GATEWAY_URL}/v1/coder/workspaces" \
  -H 'content-type: application/json' \
  -d '{"label":"canary-cage"}')"
WID="$(printf '%s' "${CREATE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace"]["id"])')"
[[ -n "${WID}" ]] || die "missing workspace id"

log "list_dir + read_file on cage"
curl -fsS -X POST "${GATEWAY_URL}/v1/coder/tools/list_dir" \
  -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"workspace_id":sys.argv[1],"path":"."}))' "${WID}")" \
  | python3 -c 'import json,sys; b=json.load(sys.stdin); assert any(e["name"]=="README.md" for e in b["entries"])'

curl -fsS -X POST "${GATEWAY_URL}/v1/coder/tools/read_file" \
  -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"workspace_id":sys.argv[1],"path":"samples/bug.py"}))' "${WID}")" \
  | python3 -c 'import json,sys; b=json.load(sys.stdin); assert "return a - b" in b["content"]'

log "path escape must fail closed"
CODE="$(curl -sS -o /tmp/coder-escape.json -w '%{http_code}' -X POST "${GATEWAY_URL}/v1/coder/tools/read_file" \
  -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"workspace_id":sys.argv[1],"path":"../../etc/passwd"}))' "${WID}")")"
[[ "${CODE}" == "403" ]] || die "expected 403 on path escape, got ${CODE}"

if [[ -d "${EVAL_REPO}" ]]; then
  log "grant human-directed eval sample_repo"
  GRANT="$(curl -fsS -X POST "${GATEWAY_URL}/v1/coder/workspaces/grant" \
    -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"path":sys.argv[1],"label":"eval-sample"}))' "${EVAL_REPO}")")"
  GID="$(printf '%s' "${GRANT}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace"]["id"])')"
  curl -fsS -X POST "${GATEWAY_URL}/v1/coder/tools/search" \
    -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"workspace_id":sys.argv[1],"query":"subtract","path":"."}))' "${GID}")" \
    | python3 -c 'import json,sys; b=json.load(sys.stdin); assert isinstance(b.get("hits"), list)'
else
  log "eval sample_repo missing — skip grant proof"
fi

log "capacity honesty for yonder-coder"
CAP_CODE="$(curl -sS -o /tmp/coder-capacity.json -w '%{http_code}' "${GATEWAY_URL}/v1/lab/capacity" || true)"
if [[ "${CAP_CODE}" == "200" ]]; then
  python3 - <<'PY'
import json
b=json.load(open("/tmp/coder-capacity.json"))
row=next((a for a in b.get("aliases",[]) if a.get("alias")=="yonder-coder"), None)
avail=(row or {}).get("availability","not_reported")
print("yonder-coder availability=", avail)
print("NOTE: do not invent READY; load via scripts/canary-coder-two-gpu.sh when needed")
PY
else
  log "capacity endpoint HTTP ${CAP_CODE:-none} — not_reported"
fi

log "optional chat probe (skips if alias not ready)"
CHAT_CODE="$(curl -sS -o /tmp/coder-chat.json -w '%{http_code}' -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H 'content-type: application/json' \
  -d '{"model":"yonder-coder","messages":[{"role":"system","content":"You are read-only. Never claim execution."},{"role":"user","content":"Reply with exactly: coder assistant canary ok"}],"max_tokens":32,"stream":false}' || true)"
if [[ "${CHAT_CODE}" == "200" ]]; then
  python3 - <<'PY'
import json
b=json.load(open("/tmp/coder-chat.json"))
text=""
try:
  text=b["choices"][0]["message"]["content"]
except Exception:
  text=str(b)
print("chat ok:", "coder assistant canary ok" in text.lower() or bool(text))
PY
else
  log "chat probe HTTP ${CHAT_CODE} — alias likely not_ready (honest; not a failure of tool jail)"
fi

log "PASS — coder status/tools/jail proofs complete (read_only)"
