#!/usr/bin/env bash
# Stage the pinned llama.cpp CUDA sm_70 runtime from ai1 into vendor/runtimes
# for YON-6b offline pack builds. Does not invent availability — rsync must
# succeed from a reachable lab host.
#
# Usage (VPN + ProxyJump required):
#   ./scripts/fetch-llama-runtime-from-ai1.sh
#   AI1_HOST=ai1 ./scripts/fetch-llama-runtime-from-ai1.sh /opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI1_HOST="${AI1_HOST:-ai1}"
REMOTE_PATH="${1:-/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70}"
DEST="${ROOT}/vendor/runtimes/llama.cpp-b10167-cuda-sm70"

die() { printf '[fetch-runtime] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[fetch-runtime] %s\n' "$*"; }

command -v rsync >/dev/null || die "rsync required"
command -v ssh >/dev/null || die "ssh required"

log "probing ${AI1_HOST}:${REMOTE_PATH}"
ssh -o BatchMode=yes -o ConnectTimeout=15 "${AI1_HOST}" \
  "test -x '${REMOTE_PATH}/bin/llama-server'" \
  || die "llama-server missing on ${AI1_HOST}:${REMOTE_PATH}/bin/llama-server"

mkdir -p "${DEST}"
log "rsync ${AI1_HOST}:${REMOTE_PATH}/ -> ${DEST}/"
rsync -a --delete \
  --exclude '.git' \
  "${AI1_HOST}:${REMOTE_PATH}/" "${DEST}/"

[[ -x "${DEST}/bin/llama-server" ]] || die "staged binary missing after rsync"
log "staged $(du -sh "${DEST}" | awk '{print $1}') at ${DEST}"
log "next: make offline-pack  # stages into payload/runtimes when present"
