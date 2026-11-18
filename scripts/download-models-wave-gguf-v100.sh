#!/usr/bin/env bash
# V100-ready GGUF wave: Gemma 4 QAT (12B / 26B-A4B / 31B) + Qwen3.6-27B.
# Prefer official/Unsloth GGUF over full safetensors (hundreds of GB saved).
#
# Stage on APFS, then rsync to the yonder drive (exFAT breaks HF locks).
# If /Volumes/yonder is missing, leave completed bundles under STAGE.
#
# Usage:
#   bash scripts/download-models-wave-gguf-v100.sh
#   DEST=/Volumes/yonder/model-bundles STAGE=~/yonder-model-stage \
#     HF=$(command -v hf) bash scripts/download-models-wave-gguf-v100.sh
set -euo pipefail

STAGE="${STAGE:-${HOME}/yonder-model-stage}"
DEST="${DEST:-/Volumes/yonder/model-bundles}"
LOGDIR="${LOGDIR:-}"
mkdir -p "$STAGE"

drive_ready() {
  # DEST parent must exist (e.g. /Volumes/yonder). Do not treat /Volumes alone
  # as ready — that would mkdir under a missing volume name incorrectly.
  local parent
  parent="$(dirname "$DEST")"
  [[ -d "$DEST" || ( -d "$parent" && "$parent" != "/" && "$parent" != "/Volumes" ) ]]
}

if drive_ready; then
  mkdir -p "$DEST" "$DEST/logs"
  LOGDIR="${LOGDIR:-$DEST/logs}"
else
  LOGDIR="${LOGDIR:-$STAGE/logs}"
  mkdir -p "$LOGDIR"
  echo "[warn] $DEST not available — will stage under $STAGE only"
fi

HF_BIN="${HF:-}"
if [[ -z "$HF_BIN" ]]; then
  for candidate in \
    "./.venv/bin/hf" \
    "hf" \
    "${HOME}/.local/bin/hf" \
    "$(command -v hf || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      HF_BIN="$candidate"
      break
    fi
  done
fi

run_hf() {
  if [[ -n "$HF_BIN" ]]; then
    "$HF_BIN" "$@"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    uvx --from 'huggingface_hub[cli]' hf "$@"
    return
  fi
  echo "[FATAL] no hf CLI found. Install with:" >&2
  echo "  uv tool install 'huggingface_hub[cli]'" >&2
  echo "  # or: python3 -m pip install --user 'huggingface_hub[cli]'" >&2
  exit 1
}

if [[ -n "$HF_BIN" ]]; then
  echo "[hf] using: $HF_BIN"
else
  echo "[hf] using: uvx --from huggingface_hub[cli] hf"
fi

export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-0}"
export HF_XET_HIGH_PERFORMANCE="${HF_XET_HIGH_PERFORMANCE:-1}"

# repo|local_name|include1,include2,...  (empty include = whole repo)
MODELS=(
  "google/gemma-4-12B-it-qat-q4_0-gguf|gemma-4-12B-it-qat-gguf|"
  "google/gemma-4-26B-A4B-it-qat-q4_0-gguf|gemma-4-26B-A4B-it-qat-gguf|"
  "google/gemma-4-31B-it-qat-q4_0-gguf|gemma-4-31B-it-qat-gguf|"
  "unsloth/Qwen3.6-27B-GGUF|Qwen3.6-27B-GGUF|*UD-Q4_K_XL*,mmproj-F16.gguf"
  "prism-ml/Ternary-Bonsai-27B-gguf|Ternary-Bonsai-27B-GGUF|Ternary-Bonsai-27B-Q2_0.gguf,Ternary-Bonsai-27B-mmproj-Q8_0.gguf"
)

sync_one() {
  local name="$1"
  local src="$STAGE/$name"
  if ! drive_ready; then
    echo "[hold] $name staged at $src (mount yonder drive, then re-run to rsync)"
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$src/.stage_complete"
    return 0
  fi
  local dst="$DEST/$name"
  echo "[rsync] $name -> $DEST"
  mkdir -p "$dst"
  rsync -a --exclude '.cache' --exclude '._*' "$src/" "$dst/"
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "$dst/.download_complete"
  rm -rf "$src"
  echo "[freed] local stage for $name"
}

download_one() {
  local repo="$1"
  local name="$2"
  local includes_csv="${3:-}"
  local stage="$STAGE/$name"
  local log="$LOGDIR/${name}.log"

  if drive_ready && [[ -f "$DEST/$name/.download_complete" ]]; then
    echo "[skip] $name already on drive"
    return 0
  fi

  if [[ -f "$stage/.stage_complete" ]]; then
    echo "[sync-only] $name already staged"
    sync_one "$name"
    return 0
  fi

  echo "[start] $(date -u +%H:%M:%S) $name <- $repo ${includes_csv:+(include $includes_csv)}"
  mkdir -p "$stage"
  local -a args=(download "$repo" --local-dir "$stage")
  if [[ -n "$includes_csv" ]]; then
    local IFS=','
    local -a incs
    read -r -a incs <<<"$includes_csv"
    local inc
    for inc in "${incs[@]}"; do
      [[ -n "$inc" ]] || continue
      args+=(--include "$inc")
    done
  fi
  if ! run_hf "${args[@]}" >"$log" 2>&1; then
    echo "[FAIL] download $name (see $log)"
    return 1
  fi
  echo "[dlok] $(date -u +%H:%M:%S) $name ($(du -sh "$stage" | awk '{print $1}'))"
  sync_one "$name"
  echo "[done] $(date -u +%H:%M:%S) $name"
}

fail=0
for entry in "${MODELS[@]}"; do
  IFS='|' read -r repo name includes <<<"$entry"
  if ! download_one "$repo" "$name" "$includes"; then
    fail=1
  fi
  df -h "$STAGE" 2>/dev/null | tail -1 || true
  if drive_ready; then
    df -h "$DEST" 2>/dev/null | tail -1 || true
  fi
done

echo "[summary] wave-gguf-v100 fail=$fail drive_ready=$(drive_ready && echo yes || echo no)"
echo "[next] rsync staged bundles to lab storage, then canary + promote aliases"
exit "$fail"
