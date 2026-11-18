#!/usr/bin/env bash
# Wave 2/3 V100 additions. Waits for Coder-Next to finish, then downloads.
# Stage on APFS, rsync to exFAT yonder drive. Xet fast path enabled.
set -euo pipefail

STAGE="${HOME}/yonder-model-stage"
DEST="/Volumes/yonder/model-bundles"
HF="./.venv/bin/hf"
LOGDIR="$DEST/logs"
mkdir -p "$STAGE" "$DEST" "$LOGDIR"

export HF_HUB_DISABLE_XET=0
export HF_XET_HIGH_PERFORMANCE=1

# Wait for the in-progress Coder-Next download to complete first.
CODER_MARK="$DEST/Qwen3-Coder-Next-GGUF/.download_complete"
echo "[wait] waiting for Coder-Next to finish ($CODER_MARK)"
while [[ ! -f "$CODER_MARK" ]]; do
  sleep 30
done
echo "[wait] Coder-Next complete; starting Wave 2/3 at $(date -u +%H:%M:%S)"

# repo|local_name|optional_include_glob
MODELS=(
  "Qwen/Qwen3-Reranker-4B|Qwen3-Reranker-4B|"
  "google/gemma-4-31B-it-qat-w4a16-ct|gemma-4-31B-it-qat-w4a16-ct|"
  "Qwen/Qwen3-Embedding-8B|Qwen3-Embedding-8B|"
  "unsloth/Qwen3.6-35B-A3B-GGUF|Qwen3.6-35B-A3B-GGUF|*UD-Q4_K_XL*"
  # Prefer GGUF for 27B (see download-models-wave-gguf-v100.sh). Full
  # safetensors trees are optional research copies only.
  "unsloth/Qwen3.6-27B-GGUF|Qwen3.6-27B-GGUF|*UD-Q4_K_XL*"
  "Qwen/Qwen3.5-27B|Qwen3.5-27B|"
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

echo "[summary] wave2-3 fail=$fail"
exit "$fail"
