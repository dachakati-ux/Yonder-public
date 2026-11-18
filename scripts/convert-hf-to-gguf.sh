#!/usr/bin/env bash
# Convert a Hugging Face safetensors tree to a derived GGUF under model-bundles/derived.
# Does not modify original downloads. Intended for ai1 with llama.cpp source present.
set -euo pipefail

SRC="${1:-}"
NAME="${2:-}"
OUTTYPE="${3:-q4_k_m}"
LLAMA_SRC="${LLAMA_SRC:-/opt/yonder/yonder-llama-build.ur6M7q/llama.cpp-b10167}"
ROOT="${MODEL_ROOT:-/srv/yonder-storage/model-bundles}"
CONV_ID="${CONV_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

die() { printf '[convert-gguf] ERROR: %s\n' "$*" >&2; exit 1; }
# Logs go to stderr so stdout carries only the derived artifact path.
log() { printf '[convert-gguf] %s\n' "$*" >&2; }

[[ -n "${SRC}" && -n "${NAME}" ]] || die "Usage: $0 <hf-dir|bundle-name> <output-name> [outtype]"
[[ -d "${LLAMA_SRC}" ]] || die "LLAMA_SRC missing: ${LLAMA_SRC}"
[[ -f "${LLAMA_SRC}/convert_hf_to_gguf.py" ]] || die "convert_hf_to_gguf.py missing"

if [[ -d "${SRC}" ]]; then
  SRC_DIR="${SRC}"
elif [[ -d "${ROOT}/${SRC}" ]]; then
  SRC_DIR="${ROOT}/${SRC}"
else
  die "Source not found: ${SRC}"
fi

OUT_DIR="${ROOT}/derived/${NAME}/${CONV_ID}"
mkdir -p "${OUT_DIR}"
OUT_FILE="${OUT_DIR}/${NAME}.${OUTTYPE}.gguf"
PROVENANCE="${OUT_DIR}/conversion.json"

log "source=${SRC_DIR}"
log "out=${OUT_FILE}"

if [[ -n "${CONVERT_PYTHON:-}" ]]; then
  PYTHON="${CONVERT_PYTHON}"
elif [[ -x /opt/yonder/yonder-convert-venv/bin/python ]]; then
  # Prefer the dedicated conversion venv (transformers/torch pinned for convert).
  PYTHON=/opt/yonder/yonder-convert-venv/bin/python
elif [[ -x /opt/yonder/python/python/bin/python3.12 ]]; then
  PYTHON=/opt/yonder/python/python/bin/python3.12
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  die "No conversion Python found; set CONVERT_PYTHON"
fi
[[ -x "${PYTHON}" ]] || die "Conversion Python is not executable: ${PYTHON}"
log "python=${PYTHON}"

"${PYTHON}" "${LLAMA_SRC}/convert_hf_to_gguf.py" "${SRC_DIR}" \
  --outfile "${OUT_FILE}" \
  --outtype "${OUTTYPE}"

[[ -f "${OUT_FILE}" ]] || die "conversion produced no file"

BYTES="$(wc -c < "${OUT_FILE}" | tr -d ' ')"
SHA="$(sha256sum "${OUT_FILE}" | awk '{print $1}')"
cat > "${PROVENANCE}" <<EOF
{
  "source_dir": "${SRC_DIR}",
  "output_file": "${OUT_FILE}",
  "outtype": "${OUTTYPE}",
  "conversion_id": "${CONV_ID}",
  "llama_src": "${LLAMA_SRC}",
  "size_bytes": ${BYTES},
  "sha256": "${SHA}",
  "converted_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

log "ok bytes=${BYTES} sha256=${SHA}"
printf '%s\n' "${OUT_FILE}"
