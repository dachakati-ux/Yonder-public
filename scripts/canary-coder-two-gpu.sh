#!/usr/bin/env bash
# Two-GPU Qwen3-Coder-Next canary on a lab worker via Gateway.
# On ai1, the static yonder-llama-cpp sidecar often holds ~11 GiB/GPU and must
# be stopped first so both cards have room. Prefer migrating yonder-chat to a
# worker-managed deployment before stopping the sidecar permanently.
#
# Usage (on ai1):
#   STOP_SIDECAR=1 ./scripts/canary-coder-two-gpu.sh worker-ai1 0,1
#   STOP_SIDECAR=1 ./scripts/canary-coder-two-gpu.sh worker-ai3 0,1
set -euo pipefail

WORKER_ID="${1:-worker-ai1}"
GPU_CSV="${2:-0,1}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
MODEL_ID="${MODEL_ID:-yonder-qwen3-coder-next-q4}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-8192}"
STOP_SIDECAR="${STOP_SIDECAR:-0}"
PROMPT="${PROMPT:-Reply with exactly: coder canary ok}"

die() { printf '[coder-canary] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[coder-canary] %s\n' "$*"; }

command -v curl >/dev/null || die "curl required"
command -v python3 >/dev/null || die "python3 required"

IFS=',' read -r -a GPU_INDEXES <<< "${GPU_CSV}"
[[ "${#GPU_INDEXES[@]}" -eq 2 ]] || die "two GPU indexes required (got ${GPU_CSV})"

if [[ "${STOP_SIDECAR}" == "1" ]]; then
  if systemctl is-active --quiet yonder-llama-cpp 2>/dev/null; then
    log "stopping static sidecar yonder-llama-cpp to free VRAM"
    sudo systemctl stop yonder-llama-cpp
  else
    log "sidecar already inactive (or not installed on this host)"
  fi
fi

GPU_JSON="$(python3 -c 'import json,sys; print(json.dumps([int(x) for x in sys.argv[1].split(",")]))' "${GPU_CSV}")"
LOAD_BODY="$(python3 - <<PY
import json
print(json.dumps({
  "model_definition_id": "${MODEL_ID}",
  "worker_id": "${WORKER_ID}",
  "placement_mode": "two_gpu",
  "gpu_indexes": json.loads('${GPU_JSON}'),
  "context_length": int("${CONTEXT_LENGTH}"),
}))
PY
)"

log "loading ${MODEL_ID} on ${WORKER_ID} gpus=${GPU_CSV} ctx=${CONTEXT_LENGTH}"
LOAD_RESP="$(curl -sS -X POST "${GATEWAY_URL}/api/admin/deployments/load" \
  -H 'content-type: application/json' \
  -d "${LOAD_BODY}")"
DEP_ID="$(printf '%s' "${LOAD_RESP}" | python3 -c 'import json,sys; b=json.load(sys.stdin); print((b.get("deployment") or {}).get("id") or "")')"
[[ -n "${DEP_ID}" ]] || die "load failed: ${LOAD_RESP}"

deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
  STATUS="$(curl -fsS "${GATEWAY_URL}/api/admin/deployments/${DEP_ID}" | python3 -c 'import json,sys; b=json.load(sys.stdin); print((b.get("deployment") or b).get("status",""))')"
  case "${STATUS}" in
    ready) break ;;
    failed|unavailable|cancelled) die "deployment ${DEP_ID} ended as ${STATUS}" ;;
  esac
  sleep 5
done
[[ "${STATUS}" == "ready" ]] || die "timeout waiting for ${DEP_ID}"

log "pointing alias yonder-coder -> ${DEP_ID}"
curl -fsS -X PATCH "${GATEWAY_URL}/api/admin/routes/yonder-coder" \
  -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"deployment_id":sys.argv[1],"enabled":True}))' "${DEP_ID}")" \
  >/dev/null

log "chat canary via alias yonder-coder"
CHAT_RESP="$(curl -fsS "${GATEWAY_URL}/v1/chat/completions" \
  -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"model":"yonder-coder","messages":[{"role":"user","content":sys.argv[1]}],"max_tokens":32,"stream":false}))' "${PROMPT}")")"
echo "${CHAT_RESP}" | grep -qi 'coder canary ok' \
  || die "unexpected chat response: ${CHAT_RESP}"

log "PASS deployment=${DEP_ID} alias=yonder-coder"
printf '%s\n' "${CHAT_RESP}" | python3 -c 'import json,sys; b=json.load(sys.stdin); print((b.get("choices") or [{}])[0].get("message",{}).get("content",""))' || true
