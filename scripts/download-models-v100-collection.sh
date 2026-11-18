#!/usr/bin/env bash
# V100 starter collection: stage on APFS, rsync to exFAT yonder drive.
set -euo pipefail

STAGE="${HOME}/yonder-model-stage"
DEST="/Volumes/yonder/model-bundles"
HF="./.venv/bin/hf"
LOGDIR="$DEST/logs"
mkdir -p "$STAGE" "$DEST" "$LOGDIR"

export HF_HUB_DISABLE_XET=0
export HF_XET_HIGH_PERFORMANCE=1

# repo|local_name|optional_include_glob
MODELS=(
  "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16|Nemotron-3-Nano-4B|"
  "Qwen/Qwen3-Embedding-4B|Qwen3-Embedding-4B|"
  "Qwen/Qwen3.5-9B|Qwen3.5-9B|"
  "google/gemma-4-12B-it|gemma-4-12B-it|"
  "google/gemma-4-26B-A4B-it|gemma-4-26B-A4B-it|"
  "google/gemma-4-31B-it-qat-q4_0-gguf|gemma-4-31B-it-qat-gguf|"
  "unsloth/Qwen3-Coder-Next-GGUF|Qwen3-Coder-Next-GGUF|*UD-Q4_K_XL*"
)

sync_one() {
  local name="$1"
  local src="$STAGE/$name"
  local dst="$DEST/$name"
  echo "[rsync] $name -> /Volumes/yonder"
  mkdir -p "$dst"
  rsync -a --delete --exclude '.cache' --exclude '._*' "$src/" "$dst/"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$dst/.download_complete"
  rm -rf "$src"
  echo "[freed] local stage for $name"
}

download_one() {
  local repo="$1"
  local name="$2"
  local include="${3:-}"
  local stage="$STAGE/$name"
  local log="$LOGDIR/${name}.log"

  if [[ -f "$DEST/$name/.download_complete" ]]; then
    echo "[skip] $name already on drive"
    return 0
  fi

  echo "[start] $(date -u +%H:%M:%S) $name <- $repo ${include:+(include $include)}"
  mkdir -p "$stage"
  local -a args=(download "$repo" --local-dir "$stage")
  if [[ -n "$include" ]]; then
    args+=(--include "$include")
  fi
  if ! "$HF" "${args[@]}" >"$log" 2>&1; then
    echo "[FAIL] download $name (see $log)"
    return 1
  fi
  echo "[dlok] $(date -u +%H:%M:%S) $name ($(du -sh "$stage" | awk '{print $1}'))"
  sync_one "$name"
  echo "[done] $(date -u +%H:%M:%S) $name"
}

fail=0
for entry in "${MODELS[@]}"; do
  IFS='|' read -r repo name include <<<"$entry"
  if ! download_one "$repo" "$name" "$include"; then
    fail=1
  fi
  df -h "$STAGE" /Volumes/yonder | tail -2 || true
done

echo "[summary] fail=$fail"
exit "$fail"
