#!/usr/bin/env bash
# Direct loopback canary for a llama-server model before Gateway promotion.
set -euo pipefail

MODEL_PATH="${1:-}"
ALIAS="${2:-canary}"
PROFILE="${3:-chat}"  # chat | embedding | rerank
PORT="${4:-8199}"
GPU="${CUDA_VISIBLE_DEVICES:-0}"
TENSOR_SPLIT="${TENSOR_SPLIT:-}"
BIN="${LLAMA_BIN:-/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70/bin/llama-server}"
LIB="${LD_LIBRARY_PATH:-/opt/cuda-12.6/lib64:/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70/bin}"

die() { printf '[canary] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[canary] %s\n' "$*"; }

[[ -n "${MODEL_PATH}" ]] || die "Usage: $0 <gguf> [alias] [profile] [port]"
[[ -f "${MODEL_PATH}" ]] || die "Missing model: ${MODEL_PATH}"
[[ -x "${BIN}" ]] || die "Missing binary: ${BIN}"

ARGS=(
  --model "${MODEL_PATH}"
  --alias "${ALIAS}"
  --host 127.0.0.1
  --port "${PORT}"
  --ctx-size 2048
  --n-gpu-layers 999
  --threads 8
)
case "${PROFILE}" in
  chat) ARGS+=(--jinja --reasoning off) ;;
  embedding) ARGS+=(--embedding --pooling last) ;;
  rerank) ARGS+=(--embedding --reranking --pooling rank) ;;
  *) die "Unknown profile: ${PROFILE}" ;;
esac

if [[ -n "${MMPROJ:-}" ]]; then
  ARGS+=(--mmproj "${MMPROJ}")
fi
if [[ "${GPU}" == *,* ]]; then
  if [[ -z "${TENSOR_SPLIT}" ]]; then
    IFS=',' read -r -a _GPU_LIST <<< "${GPU}"
    TENSOR_SPLIT="$(printf '1%.0s,' "${_GPU_LIST[@]}")"
    TENSOR_SPLIT="${TENSOR_SPLIT%,}"
  fi
  ARGS+=(--tensor-split "${TENSOR_SPLIT}")
elif [[ -n "${TENSOR_SPLIT}" ]]; then
  die "TENSOR_SPLIT requires comma-separated CUDA_VISIBLE_DEVICES"
fi

export CUDA_VISIBLE_DEVICES="${GPU}"
export LD_LIBRARY_PATH="${LIB}"

log "starting ${PROFILE} on :${PORT} gpus=${GPU} tensor_split=${TENSOR_SPLIT:-automatic}"
"${BIN}" "${ARGS[@]}" >/tmp/yonder-canary-"${PORT}".log 2>&1 &
PID=$!
cleanup() { kill "${PID}" 2>/dev/null || true; wait "${PID}" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 180); do
  if ! kill -0 "${PID}" 2>/dev/null; then
    die "llama-server exited early; see /tmp/yonder-canary-${PORT}.log"
  fi
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null || die "health failed; see /tmp/yonder-canary-${PORT}.log"

case "${PROFILE}" in
  chat)
    RESP="$(curl -fsS "http://127.0.0.1:${PORT}/v1/chat/completions" \
      -H 'content-type: application/json' \
      -d "{\"model\":\"${ALIAS}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: canary-ok\"}],\"max_tokens\":16,\"stream\":false}")"
    echo "${RESP}" | grep -qi 'canary' || die "chat canary content missing: ${RESP}"
    ;;
  embedding)
    RESP="$(curl -fsS "http://127.0.0.1:${PORT}/v1/embeddings" \
      -H 'content-type: application/json' \
      -d "{\"model\":\"${ALIAS}\",\"input\":\"yonder embedding canary\"}")"
    echo "${RESP}" | grep -q '"embedding"' || die "embedding missing: ${RESP}"
    ;;
  rerank)
    RESP="$(curl -fsS "http://127.0.0.1:${PORT}/v1/rerank" \
      -H 'content-type: application/json' \
      -d "{\"model\":\"${ALIAS}\",\"query\":\"capital of France\",\"documents\":[\"Paris is the capital of France.\",\"The sky is blue.\"]}")"
    echo "${RESP}" | grep -Eqi 'score|relevance' || die "rerank scores missing: ${RESP}"
    ;;
esac

log "PASS profile=${PROFILE} alias=${ALIAS}"
