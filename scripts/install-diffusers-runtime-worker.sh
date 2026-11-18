#!/usr/bin/env bash
# Offline install of CUDA torch + Diffusers into a lab worker's runtime.
#
# Works on ai1 (local RW storage) and ai3/ai7 (NFS model bundles read-only).
# Wheels are expected under /srv/yonder-storage/venvs/diffusers-wheels (shared).
#
# Usage (as root / sudo on the target worker):
#   bash scripts/install-diffusers-runtime-worker.sh
#
# Env knobs:
#   WHEEL_DIR          wheel cache (default: shared NFS cache)
#   SYSTEM_VENV        systemd worker venv (default: /opt/yonder/venv-worker)
#   STORAGE_VENV       optional shared canary venv (skipped when not writable)
#   SKIP_STORAGE_VENV  set to 1 to skip storage venv entirely
#   OUTPUT_ROOT        local writable Diffusers output (default: /var/lib/yonder/generated)
#   ENV_FILE           worker env file to upsert (default: /etc/yonder/worker.env)
#   RESTART_WORKER     set to 1 to restart yonder-worker after install
set -euo pipefail

WHEEL_DIR="${WHEEL_DIR:-/srv/yonder-storage/venvs/diffusers-wheels}"
STORAGE_VENV="${STORAGE_VENV:-/srv/yonder-storage/venvs/diffusers-cuda}"
# ai1 uses a dedicated venv-worker; ai3/ai7 currently run yonder-worker from /opt/yonder/venv.
if [[ -z "${SYSTEM_VENV:-}" ]]; then
  if [[ -x /opt/yonder/venv-worker/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv-worker
  elif [[ -x /opt/yonder/venv/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv
  else
    SYSTEM_VENV=/opt/yonder/venv-worker
  fi
fi
PY_BOOT="${PY_BOOT:-/opt/yonder/python/python/bin/python}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/var/lib/yonder/generated}"
ENV_FILE="${ENV_FILE:-/etc/yonder/worker.env}"
SKIP_STORAGE_VENV="${SKIP_STORAGE_VENV:-0}"
RESTART_WORKER="${RESTART_WORKER:-0}"
LOG="${LOG:-/tmp/yonder-diffusers-runtime-install.log}"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"

die() { printf '[diffusers-install] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[diffusers-install] %s\n' "$*" | tee -a "${LOG}"; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo) to mutate ${SYSTEM_VENV}"
[[ -x "${PY_BOOT}" ]] || die "missing bootstrap python: ${PY_BOOT}"
[[ -d "${WHEEL_DIR}" ]] || die "missing wheel cache: ${WHEEL_DIR}"
[[ -x "${SYSTEM_VENV}/bin/python" ]] || die "missing system worker venv python: ${SYSTEM_VENV}/bin/python"
shopt -s nullglob
wheels=("${WHEEL_DIR}"/*.whl)
[[ ${#wheels[@]} -gt 0 ]] || die "no .whl files under ${WHEEL_DIR}"

PACKAGES=(
  'torch==2.6.0+cu126'
  'nvidia-cuda-nvrtc-cu12==12.6.77'
  'nvidia-cuda-runtime-cu12==12.6.77'
  'nvidia-cuda-cupti-cu12==12.6.80'
  'nvidia-cudnn-cu12==9.5.1.17'
  'nvidia-cublas-cu12==12.6.4.1'
  'nvidia-cufft-cu12==11.3.0.4'
  'nvidia-curand-cu12==10.3.7.77'
  'nvidia-cusolver-cu12==11.7.1.2'
  'nvidia-cusparse-cu12==12.5.4.2'
  'nvidia-cusparselt-cu12==0.6.3'
  'nvidia-nccl-cu12==2.21.5'
  'nvidia-nvtx-cu12==12.6.77'
  'nvidia-nvjitlink-cu12==12.6.85'
  'triton==3.2.0'
  'diffusers==0.39.0'
  'transformers==4.55.0'
  'accelerate==1.2.1'
  safetensors pillow imageio imageio-ffmpeg sentencepiece protobuf einops
)

upsert_env() {
  local key="$1" value="$2" tmp
  [[ -f "${ENV_FILE}" ]] || die "missing env file: ${ENV_FILE}"
  tmp="$(mktemp)"
  grep -v "^${key}=" "${ENV_FILE}" > "${tmp}" || true
  printf '%s=%s\n' "${key}" "${value}" >> "${tmp}"
  cat "${tmp}" > "${ENV_FILE}"
  rm -f "${tmp}"
}

smoke() {
  local py="$1"
  "${py}" - <<'PY'
import torch, diffusers
assert torch.cuda.is_available(), "cuda unavailable"
print("torch", torch.__version__, "cuda", torch.version.cuda, "device", torch.cuda.get_device_name(0))
print("diffusers", diffusers.__version__)
for name in ("StableDiffusionXLPipeline", "DiffusionPipeline", "CogVideoXPipeline", "WanPipeline"):
    print(name, "yes" if hasattr(diffusers, name) else "MISSING")
PY
}

log "host=$(hostname -f 2>/dev/null || hostname) system_venv=${SYSTEM_VENV}"

# Optional shared canary venv — only when the storage tree is writable (ai1).
if [[ "${SKIP_STORAGE_VENV}" != "1" ]]; then
  if mkdir -p "$(dirname "${STORAGE_VENV}")" 2>/dev/null \
    && touch "$(dirname "${STORAGE_VENV}")/.yonder-write-test" 2>/dev/null; then
    rm -f "$(dirname "${STORAGE_VENV}")/.yonder-write-test"
    if [[ ! -x "${STORAGE_VENV}/bin/python" ]]; then
      log "creating storage venv ${STORAGE_VENV}"
      "${PY_BOOT}" -m venv "${STORAGE_VENV}"
    fi
    log "installing into storage venv ${STORAGE_VENV}"
    "${STORAGE_VENV}/bin/pip" install --no-index --find-links="${WHEEL_DIR}" \
      "${PACKAGES[@]}" 2>&1 | tee -a "${LOG}"
    if [[ -d /opt/yonder/wheelhouse ]]; then
      "${STORAGE_VENV}/bin/pip" install --no-index --find-links=/opt/yonder/wheelhouse \
        pydantic pydantic-settings 2>&1 | tee -a "${LOG}" || true
    fi
    chmod -R a+rX "${STORAGE_VENV}" || true
    log "smoke storage venv"
    smoke "${STORAGE_VENV}/bin/python"
  else
    log "storage tree not writable — skipping storage venv (expected on NFS clients)"
  fi
fi

log "installing into system worker venv ${SYSTEM_VENV}"
"${SYSTEM_VENV}/bin/pip" install --no-index --find-links="${WHEEL_DIR}" \
  "${PACKAGES[@]}" 2>&1 | tee -a "${LOG}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${SYSTEM_VENV}"
chmod -R u+rwX "${SYSTEM_VENV}"

log "preparing local output root ${OUTPUT_ROOT}"
mkdir -p "${OUTPUT_ROOT}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${OUTPUT_ROOT}"
chmod 0755 "${OUTPUT_ROOT}"

if [[ -f "${ENV_FILE}" ]]; then
  log "upserting Diffusers runtime env in ${ENV_FILE}"
  upsert_env YONDER_DIFFUSERS_RUNTIME__ENABLED true
  upsert_env YONDER_DIFFUSERS_RUNTIME__DRY_RUN false
  upsert_env YONDER_DIFFUSERS_RUNTIME__OUTPUT_ROOT "${OUTPUT_ROOT}"
  upsert_env YONDER_DIFFUSERS_RUNTIME__DEVICE cuda
  upsert_env YONDER_DIFFUSERS_RUNTIME__DTYPE float16
  chown root:"${SERVICE_GROUP}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
fi

log "smoke system worker venv as ${SERVICE_USER}"
if ! sudo -u "${SERVICE_USER}" "${SYSTEM_VENV}/bin/python" -c \
  'import torch,diffusers; assert torch.cuda.is_available(); print(torch.__version__, diffusers.__version__, torch.cuda.device_count())'; then
  smoke "${SYSTEM_VENV}/bin/python"
fi

if [[ "${RESTART_WORKER}" == "1" ]]; then
  log "restarting yonder-worker"
  systemctl restart yonder-worker
  for attempt in $(seq 1 20); do
    sleep 1
    if curl -fsS --max-time 4 http://127.0.0.1:8090/health >/dev/null; then
      log "worker healthy"
      break
    fi
    [[ "${attempt}" -lt 20 ]] || die "worker failed to become healthy"
  done
fi

log "done"
