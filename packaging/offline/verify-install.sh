#!/usr/bin/env bash
# Post-install verification for offline Yonder hosts.
#
#   sudo bash verify-install.sh --role control-plane
#   sudo bash verify-install.sh --role worker [--gateway-url http://...]
set -euo pipefail

ROLE=""
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
WORKER_URL="${WORKER_URL:-http://127.0.0.1:8090}"
CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:3000}"
MODEL_ROOT=""
FAILURES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --gateway-url) GATEWAY_URL="$2"; shift 2 ;;
    --worker-url) WORKER_URL="$2"; shift 2 ;;
    --console-url) CONSOLE_URL="$2"; shift 2 ;;
    --model-root) MODEL_ROOT="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,10p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "${ROLE}" ]] || { echo "--role control-plane|worker required" >&2; exit 1; }

ok() { printf '[ok] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }

check_unit() {
  local unit="$1"
  if systemctl is-active --quiet "${unit}"; then
    ok "systemd ${unit} active"
  else
    fail "systemd ${unit} not active"
  fi
}

check_http() {
  local label="$1"
  local url="$2"
  if ! command -v curl >/dev/null 2>&1; then
    fail "curl missing (needed to probe ${label})"
    return
  fi
  if curl -fsS --max-time 5 "${url}" >/dev/null; then
    ok "${label} ${url}"
  else
    fail "${label} ${url}"
  fi
}

case "${ROLE}" in
  control-plane)
    check_unit yonder-gateway.service
    check_unit yonder-console.service
    if [[ -n "${MODEL_ROOT}" ]]; then
      if [[ -d "${MODEL_ROOT}" ]]; then
        ok "model root exists (${MODEL_ROOT})"
      else
        fail "model root missing (${MODEL_ROOT})"
      fi
      if runuser -u yonder -- test -r "${MODEL_ROOT}" 2>/dev/null; then
        ok "model root readable by yonder"
      else
        fail "model root not readable by yonder (${MODEL_ROOT})"
      fi
      if runuser -u yonder -- test -w "${MODEL_ROOT}" 2>/dev/null; then
        ok "model root writable by yonder"
      else
        fail "model root not writable by yonder (${MODEL_ROOT})"
      fi
      if command -v findmnt >/dev/null 2>&1; then
        ok "model root filesystem: $(findmnt -n -o FSTYPE,SOURCE -T "${MODEL_ROOT}")"
      fi
    fi
    check_http gateway-health "${GATEWAY_URL%/}/health"
    check_http gateway-ready "${GATEWAY_URL%/}/ready"
    check_http console "${CONSOLE_URL}"
    ;;
  worker)
    check_unit yonder-worker.service
    check_http worker-health "${WORKER_URL%/}/health"
    check_http worker-ready "${WORKER_URL%/}/ready"
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS --max-time 5 "${GATEWAY_URL%/}/health" >/dev/null; then
        ok "gateway reachable from worker host (${GATEWAY_URL})"
      else
        fail "gateway not reachable from worker host (${GATEWAY_URL})"
      fi
    fi
    ;;
  *)
    echo "Unknown role: ${ROLE}" >&2
    exit 1
    ;;
esac

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "Verification failed with ${FAILURES} issue(s)." >&2
  exit 1
fi
echo "Verification passed for role=${ROLE}"
