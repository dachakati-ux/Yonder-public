#!/usr/bin/env bash
# Stage on APFS, then rsync to exFAT yonder drive (HF locks break on exFAT).
set -euo pipefail

STAGE="${HOME}/yonder-model-stage"
DEST="/Volumes/yonder/model-bundles"
HF="./.venv/bin/hf"
LOGDIR="$DEST/logs"
mkdir -p "$STAGE" "$DEST" "$LOGDIR"

export HF_HUB_DISABLE_XET=1

MODELS=(
  "Qwen/Qwen3.5-9B|Qwen3.5-9B"
  "google/gemma-4-31B-it|gemma-4-31B-it"
  "Qwen/Qwen3.6-35B-A3B|Qwen3.6-35B-A3B"
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
  echo "[dlok] $(date -u +%H:%M:%S) $name ($(du -sh "$stage" | awk '{print $1}'))"
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
  df -h "$STAGE" /Volumes/yonder | tail -2 || true
done

echo "[summary] fail=$fail"
exit "$fail"
