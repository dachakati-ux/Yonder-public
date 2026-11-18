#!/usr/bin/env bash
# Download models to local APFS, then rsync to exFAT yonder drive.
# exFAT breaks huggingface file locks, so staging locally is required.
set -euo pipefail

STAGE="${HOME}/yonder-model-stage"
DEST="/Volumes/yonder/model-bundles"
HF="./.venv/bin/hf"
LOGDIR="$DEST/logs"
mkdir -p "$STAGE" "$DEST" "$LOGDIR"

MODELS=(
  "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16|Nemotron-3-Nano-4B"
  "Qwen/Qwen3.5-9B|Qwen3.5-9B"
  "google/gemma-4-12B-it|gemma-4-12B-it"
  "google/gemma-4-12B-it-qat-q4_0-unquantized|gemma-4-12B-it-qat"
  "google/gemma-4-26B-A4B-it|gemma-4-26B-A4B-it"
  "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized|gemma-4-26B-A4B-it-qat"
  "Qwen/Qwen3.6-27B|Qwen3.6-27B"
  "google/gemma-4-31B-it|gemma-4-31B-it"
  "google/gemma-4-31B-it-qat-q4_0-unquantized|gemma-4-31B-it-qat"
  "Qwen/Qwen3.6-35B-A3B|Qwen3.6-35B-A3B"
  "Qwen/Qwen3.5-35B-A3B|Qwen3.5-35B-A3B"
  "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8|Nemotron-3-Super-120B-A12B-FP8"
  "Qwen/Qwen3-Coder-Next|Qwen3-Coder-Next"
)

sync_one() {
  local name="$1"
  local src="$STAGE/$name"
  local dst="$DEST/$name"
  echo "[rsync] $name -> drive"
  mkdir -p "$dst"
  rsync -a --delete --exclude '.cache' --exclude '._*' "$src/" "$dst/"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$dst/.download_complete"
  # free local space after successful copy
  rm -rf "$src"
  echo "[freed] local stage for $name"
}

download_one() {
  local repo="$1"
  local name="$2"
  local stage="$STAGE/$name"
  local log="$LOGDIR/${name}.log"

  if [[ -f "$DEST/$name/.download_complete" ]]; then
    echo "[skip] $name already on drive"
    return 0
  fi

  echo "[start] $(date -u +%H:%M:%S) $name <- $repo"
  mkdir -p "$stage"
  if ! "$HF" download "$repo" --local-dir "$stage" >"$log" 2>&1; then
    echo "[FAIL] download $name (see $log)"
    return 1
  fi
  echo "[dlok] $(date -u +%H:%M:%S) $name"
  sync_one "$name"
  echo "[done] $(date -u +%H:%M:%S) $name"
}

fail=0
for entry in "${MODELS[@]}"; do
  repo="${entry%%|*}"
  name="${entry##*|}"
  if ! download_one "$repo" "$name"; then
    fail=1
  fi
  df -h "$STAGE" /Volumes/yonder | tail -2
done

echo "[summary] fail=$fail"
exit "$fail"
