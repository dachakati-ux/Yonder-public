#!/usr/bin/env bash
# Sync PrismML llama.cpp sm_70 runtime from ai1 to worker-ai3 / worker-ai7.
#
# Usage (from Mac or ai1, with agent forwarding):
#   ./scripts/sync-prismml-runtime-to-workers.sh
#   HOSTS='ai3 ai7' ./scripts/sync-prismml-runtime-to-workers.sh
set -euo pipefail

SRC_HOST="${SRC_HOST:-ai1}"
REMOTE_PATH="${REMOTE_PATH:-/opt/yonder/runtimes/llama.cpp-prismml-cuda-sm70}"
HOSTS="${HOSTS:-ai3 ai7}"

log() { printf '[sync-prismml] %s\n' "$*"; }
die() { printf '[sync-prismml] ERROR: %s\n' "$*" >&2; exit 1; }

ssh -A -o BatchMode=yes -o ConnectTimeout=20 "${SRC_HOST}" \
  "test -x '${REMOTE_PATH}/bin/llama-server'" \
  || die "missing ${SRC_HOST}:${REMOTE_PATH}/bin/llama-server"

for host in ${HOSTS}; do
  log "sync ${SRC_HOST}:${REMOTE_PATH}/ -> ${host}:${REMOTE_PATH}/"
  ssh -A -o BatchMode=yes "${host}" "sudo mkdir -p '${REMOTE_PATH}'"
  # Stream via ai1 when Mac cannot reach worker disks as root; prefer direct.
  ssh -A -o BatchMode=yes "${SRC_HOST}" \
    "sudo tar -C '${REMOTE_PATH}' -cf - ." \
    | ssh -A -o BatchMode=yes "${host}" \
      "sudo tar -C '${REMOTE_PATH}' -xf - && sudo chmod -R a+rX '${REMOTE_PATH}'"
  # /opt/yonder is often 0750 yonder:yonder — verify as root/service user.
  ssh -A -o BatchMode=yes "${host}" \
    "sudo test -x '${REMOTE_PATH}/bin/llama-server' && sudo '${REMOTE_PATH}/bin/llama-server' --version | head -2 && sudo cat '${REMOTE_PATH}/PRISMML_COMMIT'" \
    || die "verify failed on ${host}"
  log "ok ${host}"
done
log DONE
