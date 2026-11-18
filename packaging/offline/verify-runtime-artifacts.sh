#!/usr/bin/env bash
# Fail-closed verification for installed or staged GPU runtime artifacts.
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PAYLOAD="${ROOT}/payload"
FAIL=0

ok() { printf '[runtime-verify] available: %s\n' "$*"; }
missing() { printf '[runtime-verify] not_reported: %s\n' "$*" >&2; FAIL=1; }
bad() { printf '[runtime-verify] collection_failed: %s\n' "$*" >&2; FAIL=1; }

if [[ -f "${ROOT}/MANIFEST.sha256" ]]; then
  (cd "${ROOT}" && sha256sum -c MANIFEST.sha256 >/dev/null) \
    && ok "payload checksums" || bad "payload checksum mismatch"
else
  missing "MANIFEST.sha256"
fi

LLAMA="${PAYLOAD}/runtimes/llama.cpp-b10167-cuda-sm70/bin/llama-server"
[[ -x "${LLAMA}" ]] || LLAMA="/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70/bin/llama-server"
if [[ -x "${LLAMA}" ]]; then
  if command -v ldd >/dev/null 2>&1; then
    LDD_OUTPUT="$(LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/opt/cuda-12.6/lib64:$(dirname "${LLAMA}")}" ldd "${LLAMA}" 2>&1 || true)"
    printf '%s\n' "${LDD_OUTPUT}" | grep -q 'not found' \
      && bad "CUDA/shared library loading for ${LLAMA}" \
      || ok "CUDA/shared library loading for ${LLAMA}"
  else
    missing "ldd for CUDA library verification"
  fi
  [[ "${LLAMA}" == *sm70* ]] \
    && ok "sm_70 runtime path annotation" \
    || bad "llama.cpp path does not identify sm_70"
else
  missing "sm_70 llama-server"
fi

PYTHON="${YONDER_PYTHON:-/opt/yonder/python/python/bin/python3.12}"
if [[ -x "${PYTHON}" ]]; then
  for module in torch diffusers; do
    if "${PYTHON}" -c "import ${module}; print(${module}.__version__)" >/dev/null 2>&1; then
      version="$("${PYTHON}" -c "import ${module}; print(${module}.__version__)")"
      ok "${module}=${version}"
    else
      missing "${module} import/version"
    fi
  done
else
  missing "Yonder Python runtime"
fi

UNIT_ROOT="${PAYLOAD}/systemd"
[[ -d "${UNIT_ROOT}" ]] || UNIT_ROOT="/etc/systemd/system"
if compgen -G "${UNIT_ROOT}/yonder-*.service" >/dev/null; then
  grep -Rqs -- '--host 127.0.0.1\|127.0.0.1:' "${UNIT_ROOT}" \
    && ok "runtime loopback binding note" \
    || bad "runtime units do not demonstrate loopback binding"
else
  missing "generation/runtime systemd units"
fi

ENV_ROOT="${PAYLOAD}/env"
[[ -d "${ENV_ROOT}" ]] || ENV_ROOT="/etc/yonder"
grep -Rqs 'MODEL_ROOT\|ALLOWED_MODEL_ROOT' "${ENV_ROOT}" \
  && ok "model-root confinement configuration" \
  || missing "model-root confinement configuration"

if [[ -d "${PAYLOAD}" ]]; then
  if find "${PAYLOAD}" -type f -perm -0002 -print -quit | grep -q .; then
    bad "world-writable payload artifact"
  else
    ok "artifact permissions (no world-writable files)"
  fi
else
  missing "payload directory"
fi

[[ "${FAIL}" -eq 0 ]] || exit 1
