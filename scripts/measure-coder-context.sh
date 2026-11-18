#!/usr/bin/env bash
# K3 — measure real KV-cache VRAM headroom for the coder lane on ai1.
#
# 8K context is the binding capability constraint for the coder model. This
# script measures actual VRAM use at increasing context lengths on the
# two-V100 split so the model manifest can record evidence: measured (not
# research_estimate). It does NOT change any config — it observes and reports.
#
# Run ON ai1 (or the coder worker host) with the coder model bundle present.
# Requires: llama.cpp server binary, nvidia-smi, the GGUF bundle.
#
# Usage:
#   MODEL=/srv/yonder-storage/model-bundles/Qwen3-Coder-Next-GGUF/...UD-Q4_K_XL.gguf \
#   LLAMA_SERVER=/opt/llama.cpp/llama-server \
#   scripts/measure-coder-context.sh
#
# Output: a table of ctx -> VRAM per GPU + whether the server stayed up.
set -euo pipefail

MODEL="${MODEL:?set MODEL to the coder GGUF path}"
LLAMA_SERVER="${LLAMA_SERVER:-llama-server}"
PORT="${PORT:-9911}"
NGL="${NGL:-999}"          # offload all layers
TENSOR_SPLIT="${TENSOR_SPLIT:-1,1}"   # two V100s
KV_TYPE="${KV_TYPE:-q8_0}" # quantized KV to stretch context
CONTEXTS="${CONTEXTS:-8192 16384 24576 32768}"

echo "== Coder context/VRAM measurement =="
echo "model:        $MODEL"
echo "server:       $LLAMA_SERVER"
echo "tensor_split: $TENSOR_SPLIT   kv_type: $KV_TYPE   ngl: $NGL"
echo "contexts:     $CONTEXTS"
echo

baseline_vram() {
  nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits
}

echo "-- baseline VRAM (before load) --"
baseline_vram
echo

printf '%-8s %-10s %-40s %s\n' "ctx" "kv_type" "vram_used_per_gpu(MiB)" "server"
for CTX in $CONTEXTS; do
  # Start server in background with this context length.
  "$LLAMA_SERVER" \
    -m "$MODEL" \
    -c "$CTX" \
    -ngl "$NGL" \
    --tensor-split "$TENSOR_SPLIT" \
    --cache-type-k "$KV_TYPE" \
    --cache-type-v "$KV_TYPE" \
    --port "$PORT" \
    --host 127.0.0.1 \
    >/tmp/llama-measure-$CTX.log 2>&1 &
  SERVER_PID=$!

  # Wait up to 120s for the server to report ready (or die).
  UP="no"
  for _ in $(seq 1 120); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      UP="crashed"
      break
    fi
    if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      UP="ready"
      break
    fi
    sleep 1
  done

  if [ "$UP" = "ready" ]; then
    sleep 3  # let KV cache settle
    VRAM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | paste -sd'/' -)
  else
    VRAM="n/a"
  fi
  printf '%-8s %-10s %-40s %s\n' "$CTX" "$KV_TYPE" "$VRAM" "$UP"

  # Tear down before the next context length.
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  sleep 2
done

echo
echo "Record the largest ctx that stayed 'ready' within 32GB/GPU headroom in"
echo "config/models/yonder-qwen3-coder-next-q4.yaml as:"
echo "  requirements.placement_limits.two_gpu.max_context_length: <ctx>"
echo "  requirements.placement_limits.two_gpu.evidence: measured"
echo "Logs: /tmp/llama-measure-<ctx>.log"
