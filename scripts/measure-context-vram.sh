#!/usr/bin/env bash
# Measure peak GPU memory.used for a model at candidate context lengths via
# Gateway load/unload + admin hardware inventory. Does not invent numbers:
# missing free/used metrics fail closed.
#
# Example (on ai1, or through tunnel):
#   GATEWAY_URL=http://127.0.0.1:8080 \
#   ./scripts/measure-context-vram.sh \
#     yonder-nemotron-3-nano-4b worker-ai3 single_gpu 0 \
#     2048,8192,16384,32768,65536
#
# Writes a TSV under /tmp/yonder-ctx-measure-<stamp>.tsv and prints a YAML
# snippet operators can paste into placement_limits after review.
set -euo pipefail

MODEL_ID="${1:-}"
WORKER_ID="${2:-}"
MODE="${3:-single_gpu}"
GPU_CSV="${4:-0}"
CTX_CSV="${5:-}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
POLL_SECONDS="${POLL_SECONDS:-5}"
READY_TIMEOUT="${READY_TIMEOUT:-600}"
SETTLE_SECONDS="${SETTLE_SECONDS:-8}"

die() { printf '[measure-ctx] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[measure-ctx] %s\n' "$*"; }

[[ -n "${MODEL_ID}" && -n "${WORKER_ID}" && -n "${GPU_CSV}" ]] \
  || die "Usage: $0 <model_definition_id> <worker_id> <single_gpu|two_gpu> <gpu_indexes_csv> [ctx_csv]"
command -v curl >/dev/null || die "curl required"
command -v python3 >/dev/null || die "python3 required"

IFS=',' read -r -a GPU_INDEXES <<< "${GPU_CSV}"
if [[ "${MODE}" == "single_gpu" ]]; then
  [[ "${#GPU_INDEXES[@]}" -eq 1 ]] || die "single_gpu requires exactly one GPU index"
elif [[ "${MODE}" == "two_gpu" ]]; then
  [[ "${#GPU_INDEXES[@]}" -eq 2 ]] || die "two_gpu requires exactly two GPU indexes"
else
  die "MODE must be single_gpu or two_gpu"
fi

if [[ -z "${CTX_CSV}" ]]; then
  case "${MODE}" in
    single_gpu) CTX_CSV="2048,4096,8192,16384,32768,65536" ;;
    two_gpu) CTX_CSV="2048,4096,8192,16384,32768,65536,131072" ;;
  esac
fi
IFS=',' read -r -a CONTEXTS <<< "${CTX_CSV}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_TSV="/tmp/yonder-ctx-measure-${MODEL_ID}-${MODE}-${STAMP}.tsv"
printf 'model\tworker\tmode\tgpus\tcontext\tstatus\tpeak_used_mib_csv\tpeak_used_total_mib\tnotes\n' \
  >"${OUT_TSV}"

gpu_json_array() {
  python3 - <<'PY' "${GPU_CSV}"
import json, sys
print(json.dumps([int(x) for x in sys.argv[1].split(",") if x.strip() != ""]))
PY
}

admin_get() {
  curl -fsS "${GATEWAY_URL}$1"
}

admin_post() {
  local path="$1"
  local body="$2"
  curl -fsS -X POST "${GATEWAY_URL}${path}" \
    -H 'content-type: application/json' \
    -d "${body}"
}

memory_used_mib() {
  # Prints comma-separated MiB for selected GPU indexes, or empty on failure.
  admin_get /api/admin/hardware | python3 -c '
import json, sys
worker_id = sys.argv[1]
indexes = {int(x) for x in sys.argv[2].split(",") if x.strip()}
payload = json.load(sys.stdin)
nodes = payload.get("nodes") or []
target = next((n for n in nodes if n.get("worker_id") == worker_id), None)
if target is None:
    raise SystemExit(0)
hw = target.get("hardware") or {}
gpus = hw.get("gpus") or []
values = []
for gpu in gpus:
    idx_metric = gpu.get("index") or {}
    idx = idx_metric.get("value") if isinstance(idx_metric, dict) else idx_metric
    if idx is None:
        continue
    idx = int(idx)
    if idx not in indexes:
        continue
    used = gpu.get("memory_used_bytes") or {}
    avail = used.get("availability") if isinstance(used, dict) else "available"
    raw = used.get("value") if isinstance(used, dict) else used
    if avail != "available" or raw is None:
        raise SystemExit(0)
    values.append((idx, int(raw) // (1024 * 1024)))
values.sort()
if len(values) != len(indexes):
    raise SystemExit(0)
print(",".join(str(v) for _, v in values))
' "${WORKER_ID}" "${GPU_CSV}"
}

wait_ready() {
  local deployment_id="$1"
  local deadline=$((SECONDS + READY_TIMEOUT))
  while (( SECONDS < deadline )); do
    local status
    status="$(admin_get "/api/admin/deployments/${deployment_id}" | python3 -c '
import json,sys
body=json.load(sys.stdin)
dep=body.get("deployment") or body
print(dep.get("status",""))
')"
    case "${status}" in
      ready) return 0 ;;
      failed|unavailable|cancelled) return 1 ;;
    esac
    sleep "${POLL_SECONDS}"
  done
  return 1
}

unload_quiet() {
  local deployment_id="$1"
  curl -sS -X POST "${GATEWAY_URL}/api/admin/deployments/${deployment_id}/unload" >/dev/null || true
}

# Wait until selected GPUs report near-idle used memory so the next load's
# free-memory preflight is not racing an incomplete unload (or a sibling
# ladder on the same host).
wait_gpus_idle() {
  local deadline=$((SECONDS + READY_TIMEOUT))
  local used
  local max_idle_mib="${IDLE_USED_MIB:-512}"
  while (( SECONDS < deadline )); do
    used="$(memory_used_mib || true)"
    if [[ -n "${used}" ]]; then
      if python3 - <<PY
vals = [int(x) for x in "${used}".split(",") if x.strip() != ""]
raise SystemExit(0 if vals and all(v <= ${max_idle_mib} for v in vals) else 1)
PY
      then
        log "gpus idle used_mib=${used}"
        return 0
      fi
      log "waiting for idle (used_mib=${used}, want<=${max_idle_mib})"
    else
      log "waiting for idle (memory_used not_reported)"
    fi
    sleep "${POLL_SECONDS}"
  done
  return 1
}

log "gateway=${GATEWAY_URL} model=${MODEL_ID} worker=${WORKER_ID} mode=${MODE} gpus=${GPU_CSV}"
log "contexts=${CTX_CSV}"
log "tsv=${OUT_TSV}"

GPU_JSON="$(gpu_json_array)"
LAST_OK_CTX=""

for CTX in "${CONTEXTS[@]}"; do
  CTX="$(echo "${CTX}" | tr -d '[:space:]')"
  [[ -n "${CTX}" ]] || continue
  log "--- context ${CTX} ---"
  if ! wait_gpus_idle; then
    die "GPUs ${GPU_CSV} on ${WORKER_ID} never returned to idle before ctx=${CTX}"
  fi
  BASELINE="$(memory_used_mib || true)"
  [[ -n "${BASELINE}" ]] || die "Could not read baseline memory_used for ${WORKER_ID} gpus=${GPU_CSV} (not_reported)"

  LOAD_BODY="$(python3 - <<PY
import json
print(json.dumps({
  "model_definition_id": "${MODEL_ID}",
  "worker_id": "${WORKER_ID}",
  "placement_mode": "${MODE}",
  "gpu_indexes": json.loads('${GPU_JSON}'),
  "context_length": int("${CTX}"),
}))
PY
)"

  LOAD_RESP="$(curl -sS -X POST "${GATEWAY_URL}/api/admin/deployments/load" \
    -H 'content-type: application/json' \
    -d "${LOAD_BODY}" || true)"
  DEP_ID="$(printf '%s' "${LOAD_RESP}" | python3 -c '
import json,sys
try:
    body=json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit
dep=body.get("deployment") or {}
print(dep.get("id") or "")
')"
  if [[ -z "${DEP_ID}" ]]; then
    NOTE="$(printf '%s' "${LOAD_RESP}" | python3 -c 'import json,sys
try:
  b=json.load(sys.stdin)
except Exception as exc:
  print(exc); raise SystemExit
e=b.get("error") or {}
print(e.get("message") or b)' 2>/dev/null || printf '%s' "${LOAD_RESP}")"
    log "load rejected at ctx=${CTX}: ${NOTE}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${MODEL_ID}" "${WORKER_ID}" "${MODE}" "${GPU_CSV}" "${CTX}" "rejected" "" "" "${NOTE}" \
      >>"${OUT_TSV}"
    break
  fi

  if ! wait_ready "${DEP_ID}"; then
    NOTE="never reached ready (see deployment logs for ${DEP_ID})"
    log "${NOTE}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${MODEL_ID}" "${WORKER_ID}" "${MODE}" "${GPU_CSV}" "${CTX}" "failed" "" "" "${NOTE}" \
      >>"${OUT_TSV}"
    unload_quiet "${DEP_ID}"
    wait_gpus_idle || true
    break
  fi

  sleep "${SETTLE_SECONDS}"
  PEAK="$(memory_used_mib || true)"
  # Inventory can lag; retry a few times if READY but used still looks idle.
  if [[ -n "${PEAK}" ]]; then
    for _try in 1 2 3 4 5; do
      if python3 - <<PY
base = [int(x) for x in "${BASELINE}".split(",") if x.strip() != ""]
peak = [int(x) for x in "${PEAK}".split(",") if x.strip() != ""]
raise SystemExit(0 if peak and any(p > b + 256 for p, b in zip(peak, base)) else 1)
PY
      then
        break
      fi
      sleep "${POLL_SECONDS}"
      PEAK="$(memory_used_mib || true)"
      [[ -n "${PEAK}" ]] || break
    done
  fi
  unload_quiet "${DEP_ID}"
  wait_gpus_idle || true

  if [[ -z "${PEAK}" ]]; then
    NOTE="ready but memory_used not_reported after settle"
    log "${NOTE}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${MODEL_ID}" "${WORKER_ID}" "${MODE}" "${GPU_CSV}" "${CTX}" "not_reported" "" "" "${NOTE}" \
      >>"${OUT_TSV}"
    break
  fi

  TOTAL="$(python3 - <<PY
print(sum(int(x) for x in "${PEAK}".split(",") if x))
PY
)"
  # Fail closed on bogus zero-peak when baseline was also ~0: not a real measure.
  if [[ "${TOTAL}" -le 256 ]]; then
    NOTE="ready but peak_used_total_mib=${TOTAL} looks unset (inventory lag); not promoting"
    log "${NOTE}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${MODEL_ID}" "${WORKER_ID}" "${MODE}" "${GPU_CSV}" "${CTX}" "not_reported" "${PEAK}" "${TOTAL}" "${NOTE}" \
      >>"${OUT_TSV}"
    break
  fi
  log "ctx=${CTX} peak_used_mib=${PEAK} total=${TOTAL} (baseline=${BASELINE})"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${MODEL_ID}" "${WORKER_ID}" "${MODE}" "${GPU_CSV}" "${CTX}" "ok" "${PEAK}" "${TOTAL}" "baseline=${BASELINE}" \
    >>"${OUT_TSV}"
  LAST_OK_CTX="${CTX}"
done

log "results: ${OUT_TSV}"
if [[ -n "${LAST_OK_CTX}" ]]; then
  cat <<EOF

# Suggested manifest snippet after operator review (do not invent):
# ${MODE}:
#   max_context_length: ${LAST_OK_CTX}
#   evidence: measured
# Evidence file: ${OUT_TSV}
EOF
fi
