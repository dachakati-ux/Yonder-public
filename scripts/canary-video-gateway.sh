#!/usr/bin/env bash
# Short Gateway-mediated video generation canary (txt2video).
# Run on ai1 after Diffusers video weights are available and EnsureReady is live.
#
# Usage:
#   bash scripts/canary-video-gateway.sh
#
# Env:
#   GATEWAY_URL   default http://127.0.0.1:8080
#   PROMPT        default short motion prompt
#   STEPS         default 8 (keep short on V100)
#   POLL_MAX      default 180 (×5s)
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
PROMPT="${PROMPT:-aurora curtains drifting over a dark lake, gentle camera push}"
STEPS="${STEPS:-8}"
POLL_MAX="${POLL_MAX:-180}"
export PROMPT STEPS GATEWAY_URL
LOGDIR="${LOGDIR:-${HOME}/yonder-canaries}"
mkdir -p "${LOGDIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOGDIR}/video-gateway-${STAMP}.log"
STATUS_FILE="${LOGDIR}/video-gateway-status-${STAMP}.txt"

die() { printf '[video-canary] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[video-canary] %s\n' "$*" | tee -a "${LOG}"; }

log "gateway=${GATEWAY_URL} steps=${STEPS}"
curl -fsS "${GATEWAY_URL}/health" >/dev/null || die "gateway health failed"

BODY="$(python3 -c 'import json,sys; print(json.dumps({"mode":"txt2video","prompt":sys.argv[1],"aspect_ratio":"16:9","steps":int(sys.argv[2]),"guidance":7.5}))' "${PROMPT}" "${STEPS}")"
CREATE="$(curl -fsS -X POST "${GATEWAY_URL}/v1/generation/jobs" \
  -H 'content-type: application/json' \
  -d "${BODY}")"
JOB_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["job"]["id"])' "${CREATE}")"
log "accepted job_id=${JOB_ID}"
printf '%s\n' "${CREATE}" >>"${LOG}"

for i in $(seq 1 "${POLL_MAX}"); do
  LIVE="$(curl -fsS "${GATEWAY_URL}/v1/generation/jobs/${JOB_ID}")"
  python3 -c '
import json, sys
j = json.loads(sys.argv[1])["job"]
st = j.get("status")
prog = j.get("progress") or []
last = prog[-1] if prog else {}
msg = (last.get("message") or "")[:100]
print(
    "poll",
    sys.argv[2],
    "status=" + str(st),
    "step=" + str(last.get("step")),
    "pct=" + str(last.get("percent")),
    "msg=" + msg,
)
open(sys.argv[3], "w").write(st or "")
err = j.get("error")
if err:
    print("error", str(err)[:240])
' "${LIVE}" "${i}" "${STATUS_FILE}" | tee -a "${LOG}"
  ST="$(cat "${STATUS_FILE}")"
  case "${ST}" in
    succeeded)
      log "PASS job=${JOB_ID}"
      printf '%s\n' "${LIVE}" >>"${LOG}"
      exit 0
      ;;
    failed|error|cancelled|rejected|not_supported)
      log "FAIL status=${ST} job=${JOB_ID} (see ${LOG})"
      printf '%s\n' "${LIVE}" >>"${LOG}"
      exit 1
      ;;
  esac
  sleep 5
done

die "timed out waiting for ${JOB_ID} (log ${LOG})"
