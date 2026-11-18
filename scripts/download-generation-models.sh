#!/usr/bin/env bash
# V100-oriented image/video generation weights. Waits for Wave 2/3 to finish.
set -euo pipefail

STAGE="${HOME}/yonder-model-stage"
DEST="/Volumes/yonder/model-bundles"
HF="./.venv/bin/hf"
LOGDIR="$DEST/logs"
mkdir -p "$STAGE" "$DEST" "$LOGDIR"

export HF_HUB_DISABLE_XET=0
export HF_XET_HIGH_PERFORMANCE=1

WAVE_MARK="$DEST/Qwen3.5-27B/.download_complete"
echo "[wait] waiting for Wave 2/3 to finish ($WAVE_MARK)"
while [[ ! -f "$WAVE_MARK" ]]; do
  sleep 30
done
echo "[wait] Wave 2/3 complete; starting generation models at $(date -u +%H:%M:%S)"

sync_one() {
  local name="$1"
  local src="$STAGE/$name"
  local dst="$DEST/$name"
  echo "[rsync] $name -> /Volumes/yonder"
  mkdir -p "$dst"
  rsync -a --delete --exclude '.cache' --exclude '._*' "$src/" "$dst/"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$dst/.download_complete"
  rm -rf "$src"
  echo "[done] $(date -u +%H:%M:%S) $name"
}

download_repo() {
  local repo="$1"
  local name="$2"
  shift 2
  local stage="$STAGE/$name"
  local log="$LOGDIR/${name}.log"
  if [[ -f "$DEST/$name/.download_complete" ]]; then
    echo "[skip] $name already on drive"
    return
  fi
  echo "[start] $(date -u +%H:%M:%S) $name <- $repo"
  mkdir -p "$stage"
  "$HF" download "$repo" "$@" --local-dir "$stage" >"$log" 2>&1
  echo "[dlok] $(date -u +%H:%M:%S) $name ($(du -sh "$stage" | awk '{print $1}'))"
  sync_one "$name"
}

# Single-file FP16 checkpoint avoids ~48 GB of duplicate ONNX/Flax/full-precision files.
download_repo "stabilityai/sdxl-turbo" "sdxl-turbo-fp16" \
  "sd_xl_turbo_1.0_fp16.safetensors" "LICENSE.md" "README.md"
download_repo "Tongyi-MAI/Z-Image-Turbo" "Z-Image-Turbo"
download_repo "Wan-AI/Wan2.2-TI2V-5B-Diffusers" "Wan2.2-TI2V-5B-Diffusers"
download_repo "zai-org/CogVideoX-5b" "CogVideoX-5b"

echo "[summary] generation models complete"
