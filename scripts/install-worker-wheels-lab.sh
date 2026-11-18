#!/usr/bin/env bash
# Install current Yonder worker (+ deps) wheels into a lab worker venv and restart.
#
# This is the durable replacement for site-packages hot-patches on ai3/ai7.
# Does NOT re-copy CUDA runtime or model bundles (use deploy-worker-verified-runtime.sh
# for that fuller path).
#
# Usage (as root on the target worker):
#   bash scripts/install-worker-wheels-lab.sh /path/to/wheels
#
# Env:
#   SYSTEM_VENV   override venv (default: auto-detect venv-worker then venv)
#   ENV_FILE      worker env (default /etc/yonder/worker.env)
#   RESTART       set 0 to skip systemctl restart (default 1)
set -euo pipefail

WHEEL_DIR="${1:-}"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
ENV_FILE="${ENV_FILE:-/etc/yonder/worker.env}"
RESTART="${RESTART:-1}"
UV="${UV:-/opt/yonder/uv/uv}"

die() { printf '[install-worker-wheels] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[install-worker-wheels] %s\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
[[ -n "${WHEEL_DIR}" && -d "${WHEEL_DIR}" ]] || die "Usage: $0 /path/to/wheels"
[[ -x "${UV}" ]] || die "uv missing: ${UV}"

if [[ -z "${SYSTEM_VENV:-}" ]]; then
  if [[ -x /opt/yonder/venv-worker/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv-worker
  elif [[ -x /opt/yonder/venv/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv
  else
    die "No worker venv at /opt/yonder/venv-worker or /opt/yonder/venv"
  fi
fi
VENV_PY="${SYSTEM_VENV}/bin/python"
[[ -x "${VENV_PY}" ]] || die "Missing ${VENV_PY}"

shopt -s nullglob
contracts=("${WHEEL_DIR}"/yonder_contracts-*.whl)
configuration=("${WHEEL_DIR}"/yonder_configuration-*.whl)
telemetry=("${WHEEL_DIR}"/yonder_telemetry-*.whl)
worker=("${WHEEL_DIR}"/yonder_worker-*.whl)
[[ ${#contracts[@]} -eq 1 ]] || die "need exactly one yonder_contracts wheel in ${WHEEL_DIR}"
[[ ${#configuration[@]} -eq 1 ]] || die "need exactly one yonder_configuration wheel in ${WHEEL_DIR}"
[[ ${#telemetry[@]} -eq 1 ]] || die "need exactly one yonder_telemetry wheel in ${WHEEL_DIR}"
[[ ${#worker[@]} -eq 1 ]] || die "need exactly one yonder_worker wheel in ${WHEEL_DIR}"

log "host=$(hostname -f 2>/dev/null || hostname) venv=${SYSTEM_VENV}"
log "installing ${worker[0]##*/}"

"${UV}" pip install \
  --python "${VENV_PY}" \
  --offline --no-index --no-deps \
  --find-links "${WHEEL_DIR}" \
  --reinstall-package yonder-contracts \
  --reinstall-package yonder-configuration \
  --reinstall-package yonder-telemetry \
  --reinstall-package yonder-worker \
  "${contracts[0]}" \
  "${configuration[0]}" \
  "${telemetry[0]}" \
  "${worker[0]}"

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${SYSTEM_VENV}"
chmod -R u+rwX "${SYSTEM_VENV}"

# Prove the durable markers that used to live only as hot-patches.
"${VENV_PY}" - <<'PY'
from pathlib import Path
import app.runtime.diffusers_manager as dm
import app.runtime.occupancy as occ
text = Path(dm.__file__).read_text(encoding="utf-8")
assert "callback_on_step_end" in text, "diffusers_manager missing progress callback"
assert "resolve_cuda_device" in text, "diffusers_manager missing physical CUDA bind"
assert 'os.environ["CUDA_VISIBLE_DEVICES"]' not in text, (
    "diffusers must not mutate CVD in-process"
)
assert hasattr(occ, "union_lane_counts"), "occupancy missing union_lane_counts"
print("markers_ok", dm.__file__)
PY

if [[ "${RESTART}" == "1" ]]; then
  log "restarting yonder-worker"
  systemctl restart yonder-worker
  for attempt in $(seq 1 20); do
    sleep 2
    if curl -fsS --max-time 4 http://127.0.0.1:8090/health >/dev/null; then
      break
    fi
    [[ "${attempt}" -lt 20 ]] || {
      journalctl -u yonder-worker -n 60 --no-pager || true
      die "worker did not become healthy"
    }
  done
  status="$(curl -fsS http://127.0.0.1:8090/v1/lifecycle/status)"
  printf '%s\n' "${status}" | head -c 500
  echo
  grep -q '"runtime":"unified"' <<<"${status}" ||
    die "worker healthy but managed runtime not unified"
fi

log "complete"
