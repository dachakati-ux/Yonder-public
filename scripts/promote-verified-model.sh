#!/usr/bin/env bash
# Copy a reviewed manifest into config/models only after a recorded canary PASS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${1:-}"
CANARY_LOG="${2:-}"
DEST_NAME="${3:-$(basename "${MANIFEST:-model.yaml}")}"

die() { printf '[promote-model] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[promote-model] %s\n' "$*"; }

[[ -f "${MANIFEST}" ]] || die "Usage: $0 <draft-manifest.yaml> <canary-log> [destination-name]"
[[ -f "${CANARY_LOG}" ]] || die "Canary log is required and must exist: ${CANARY_LOG}"
grep -Eq '\[canary\] PASS profile=(chat|embedding|rerank)' "${CANARY_LOG}" \
  || die "Canary log contains no recognized PASS line"
grep -Eq '^# .*operator-enabled only after|^# .*after a successful.*canary' "${MANIFEST}" \
  || die "Manifest must state that operator enablement follows canary"
[[ "${DEST_NAME}" == *.yaml || "${DEST_NAME}" == *.yml ]] \
  || die "Destination must be a YAML filename"

DEST="${ROOT}/config/models/${DEST_NAME}"
[[ ! -e "${DEST}" ]] || die "Destination already exists: ${DEST}"
install -m 0644 "${MANIFEST}" "${DEST}"
log "promoted ${DEST_NAME}; route alias creation remains a separate operator action"
log "evidence=${CANARY_LOG}"
