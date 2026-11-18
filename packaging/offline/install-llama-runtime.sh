#!/usr/bin/env bash
# Install CUDA toolkit + llama.cpp sm_70 runtime from an offline pack payload.
# Safe to re-run. Does not start inference processes.
set -euo pipefail

PACK_ROOT="${1:-.}"
DEST_CUDA="${YONDER_CUDA_PREFIX:-/opt/cuda-12.6}"
DEST_RUNTIME="${YONDER_LLAMA_RUNTIME_PREFIX:-/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70}"

log() { printf '[install-llama-runtime] %s\n' "$*"; }
die() { printf '[install-llama-runtime] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "${PACK_ROOT}/payload" ]] || die "Expected pack payload under ${PACK_ROOT}/payload"

if [[ -f "${PACK_ROOT}/payload/cuda/cuda_12.6.3_linux.run" ]]; then
  log "Installing CUDA toolkit (toolkit-only) to ${DEST_CUDA}"
  sudo bash "${PACK_ROOT}/payload/cuda/cuda_12.6.3_linux.run" \
    --silent --toolkit --toolkitpath="${DEST_CUDA}" --no-opengl-libs \
    || die "CUDA toolkit install failed"
else
  log "No payload/cuda runfile present — skipping toolkit install"
fi

if [[ -d "${PACK_ROOT}/payload/runtimes/llama.cpp-b10167-cuda-sm70" ]]; then
  log "Installing llama.cpp runtime to ${DEST_RUNTIME}"
  sudo mkdir -p "$(dirname "${DEST_RUNTIME}")"
  sudo rsync -a --delete \
    "${PACK_ROOT}/payload/runtimes/llama.cpp-b10167-cuda-sm70/" \
    "${DEST_RUNTIME}/"
  sudo chown -R yonder:yonder /opt/yonder/runtimes 2>/dev/null || true
  sudo chmod -R a+rX "${DEST_RUNTIME}"
  if command -v restorecon >/dev/null; then
    sudo restorecon -Rv /opt/yonder/runtimes >/dev/null || true
  fi
else
  log "No payload/runtimes/llama.cpp-b10167-cuda-sm70 — skipping runtime install"
fi

if [[ -f "${PACK_ROOT}/payload/systemd/yonder-llama-cpp@.service" ]]; then
  sudo cp "${PACK_ROOT}/payload/systemd/yonder-llama-cpp@.service" \
    /etc/systemd/system/yonder-llama-cpp@.service
  sudo cp "${PACK_ROOT}/payload/systemd/yonder-llama-cpp.service" \
    /etc/systemd/system/yonder-llama-cpp.service 2>/dev/null || true
  sudo systemctl daemon-reload
  log "Installed systemd llama.cpp units (disabled until operator enables)"
fi

log "Done. Enable worker llama_cpp_runtime on ai1/ai3 after model paths are reachable."
log "ai5 is abandoned; use ai7 (worker-ai7) once drivers + worker are installed."
