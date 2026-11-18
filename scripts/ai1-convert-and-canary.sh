#!/usr/bin/env bash
# Run on ai1: convert safetensors → derived GGUF (untouched originals), then canary.
# Promote only after PASS. Does not create Gateway aliases.
set -euo pipefail

ROOT_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERT="${ROOT_SCRIPTS}/convert-hf-to-gguf.sh"
CANARY="${ROOT_SCRIPTS}/canary-llama-model.sh"
EXPORT_LOG_DIR="${EXPORT_LOG_DIR:-/tmp/yonder-canaries}"
CONVERT_PYTHON="${CONVERT_PYTHON:-/opt/yonder/yonder-convert-venv/bin/python}"
export CONVERT_PYTHON

die() { printf '[ai1-batch] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[ai1-batch] %s\n' "$*"; }

[[ -x "${CONVERT}" ]] || die "missing ${CONVERT}"
[[ -x "${CANARY}" ]] || die "missing ${CANARY}"
[[ -x "${CONVERT_PYTHON}" ]] || die "missing CONVERT_PYTHON=${CONVERT_PYTHON}"
mkdir -p "${EXPORT_LOG_DIR}"

require_deps() {
  "${CONVERT_PYTHON}" - <<'PY'
import importlib
for name in ("transformers", "torch", "gguf", "sentencepiece"):
    importlib.import_module(name)
    print(name, "ok")
PY
}

convert_and_canary() {
  local bundle="$1"
  local name="$2"
  local profile="$3"
  # convert_hf_to_gguf.py emits f32/f16/bf16/q8_0/tq1_0/tq2_0 only. K-quants need
  # llama-quantize, which the installed sm_70 runtime does not ship.
  local outtype="${4:-q8_0}"
  local port="${5:-8201}"
  local gpu="${6:-0}"
  local log_file="${EXPORT_LOG_DIR}/${name}.${profile}.canary.log"

  log "CONVERT ${bundle} → ${name} (${outtype})"
  local gguf
  gguf="$(bash "${CONVERT}" "${bundle}" "${name}" "${outtype}")"
  # Worker/service user must be able to read derived artifacts.
  chmod 0644 "${gguf}" 2>/dev/null || true
  log "CANARY ${profile} ${gguf} on gpu=${gpu} port=${port}"
  set +e
  CUDA_VISIBLE_DEVICES="${gpu}" bash "${CANARY}" "${gguf}" "${name}" "${profile}" "${port}" \
    | tee "${log_file}"
  local rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    log "FAIL ${name} profile=${profile} (see ${log_file} and /tmp/yonder-canary-${port}.log)"
    return "${rc}"
  fi
  log "PASS ${name} profile=${profile} evidence=${log_file}"
}

require_deps

# Smallest / highest value first. Chat conversions may take a long time.
# Skip any step that fails; do not invent readiness.
# q8_0 sizing (measured 4B ≈ 4.3 GiB) keeps these within one V100-32GB while the
# static chat service still holds ~11 GiB per GPU.
set +e
convert_and_canary Qwen3-Reranker-4B qwen3-reranker-4b rerank q8_0 8203 0
convert_and_canary Qwen3-Embedding-8B qwen3-embedding-8b embedding q8_0 8202 0
convert_and_canary Nemotron-3-Nano-4B nemotron-3-nano-4b chat q8_0 8204 0
convert_and_canary Qwen3.5-9B qwen35-9b chat q8_0 8205 0
convert_and_canary gemma-4-12B-it gemma4-12b-it chat q8_0 8208 0
# 26B/27B bundles exceed one V100 at q8_0 (~27 GiB). They need either a two-GPU
# placement with the static service stopped, or a K-quant step that requires
# building llama-quantize. Left out until that evidence exists.
set -e

log "Batch finished. Promote only PASS evidence via scripts/promote-verified-model.sh"
log "Coder GGUF requires exclusive CUDA_VISIBLE_DEVICES=0,1 TENSOR_SPLIT=1,1 after unload."
