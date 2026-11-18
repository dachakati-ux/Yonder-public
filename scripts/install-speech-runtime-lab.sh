#!/usr/bin/env bash
# Install staged speech bundles + wheels onto a lab worker (run on target as root).
#
# Expects a stage directory produced on a networked machine:
#   stage/whisper-small/     faster-whisper CT2 model
#   stage/piper/             en_US-lessac-medium.onnx(+json) + runtime/piper
#   stage/wheels/            manylinux py3.12 faster-whisper deps
#
# Usage:
#   sudo bash scripts/install-speech-runtime-lab.sh /path/to/stage
set -euo pipefail

STAGE="${1:-}"
MODEL_ROOT="${MODEL_ROOT:-/srv/yonder-storage/model-bundles}"
UV="${UV:-/opt/yonder/uv/uv}"
ENV_FILE="${ENV_FILE:-/etc/yonder/worker.env}"
RESTART="${RESTART:-1}"

die() { printf '[install-speech] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[install-speech] %s\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root"
[[ -n "${STAGE}" && -d "${STAGE}" ]] || die "Usage: $0 /path/to/stage"
[[ -d "${STAGE}/whisper-small" ]] || die "missing ${STAGE}/whisper-small"
[[ -f "${STAGE}/piper/en_US-lessac-medium.onnx" ]] || die "missing piper voice"
[[ -x "${STAGE}/piper/runtime/piper" ]] || die "missing piper binary"
[[ -d "${STAGE}/wheels" ]] || die "missing wheels"
[[ -x "${UV}" ]] || die "uv missing: ${UV}"
mountpoint -q /srv/yonder-storage || die "/srv/yonder-storage is not a mountpoint"

if [[ -z "${SYSTEM_VENV:-}" ]]; then
  if [[ -x /opt/yonder/venv-worker/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv-worker
  elif [[ -x /opt/yonder/venv/bin/python ]]; then
    SYSTEM_VENV=/opt/yonder/venv
  else
    die "No worker venv"
  fi
fi

log "host=$(hostname -f 2>/dev/null || hostname) venv=${SYSTEM_VENV}"
mkdir -p "${MODEL_ROOT}"
rsync -a --delete "${STAGE}/whisper-small/" "${MODEL_ROOT}/whisper-small/"
rsync -a --delete "${STAGE}/piper/" "${MODEL_ROOT}/piper/"
chmod -R a+rX "${MODEL_ROOT}/whisper-small" "${MODEL_ROOT}/piper"
chmod a+x "${MODEL_ROOT}/piper/runtime/piper"

log "installing faster-whisper wheels offline"
"${UV}" pip install \
  --python "${SYSTEM_VENV}/bin/python" \
  --offline --no-index \
  --find-links "${STAGE}/wheels" \
  faster-whisper piper-tts pathvalidate

# Worker runs as yonder; ensure newly installed packages are readable.
chown -R "${SERVICE_USER:-yonder}:${SERVICE_GROUP:-yonder}" \
  "${SYSTEM_VENV}/lib"/python*/site-packages/piper \
  "${SYSTEM_VENV}/lib"/python*/site-packages/piper_tts* \
  "${SYSTEM_VENV}/lib"/python*/site-packages/pathvalidate* \
  "${SYSTEM_VENV}/lib"/python*/site-packages/faster_whisper* \
  "${SYSTEM_VENV}/lib"/python*/site-packages/ctranslate2* \
  "${SYSTEM_VENV}/lib"/python*/site-packages/onnxruntime* \
  2>/dev/null || true

# Ensure speech_runtime is enabled for this worker process (Yonder env prefix).
if [[ -f "${ENV_FILE}" ]]; then
  upsert_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "${ENV_FILE}"; then
      sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
    else
      printf '%s=%s\n' "${key}" "${val}" >> "${ENV_FILE}"
    fi
  }
  upsert_env YONDER_SPEECH_RUNTIME__ENABLED true
  upsert_env YONDER_SPEECH_RUNTIME__DEVICE cpu
  upsert_env YONDER_SPEECH_RUNTIME__PIPER_BINARY \
    "${MODEL_ROOT}/piper/runtime/piper"
  upsert_env YONDER_SPEECH_RUNTIME__PIPER_MODEL_PATH \
    "${MODEL_ROOT}/piper/en_US-lessac-medium.onnx"
  upsert_env YONDER_SPEECH_RUNTIME__WHISPER_MODEL_PATH \
    "${MODEL_ROOT}/whisper-small"
fi

if [[ "${RESTART}" == "1" ]]; then
  systemctl restart yonder-worker
  for _ in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8090/health >/dev/null 2>&1; then
      log "worker healthy"
      break
    fi
    sleep 1
  done
fi

log "complete — load STT/TTS via lifecycle next"
