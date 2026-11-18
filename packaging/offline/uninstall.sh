#!/usr/bin/env bash
# Stop and remove Yonder offline install units/files (keeps /var/lib/yonder data by default).
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${PACK_ROOT}/payload/scripts/lib.sh"

PURGE_DATA=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-data) PURGE_DATA=1; shift ;;
    *) die "Unknown arg: $1" ;;
  esac
done

require_root

for unit in yonder-gateway yonder-console yonder-worker; do
  if systemctl list-unit-files | grep -q "^${unit}.service"; then
    systemctl disable --now "${unit}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/${unit}.service"
  fi
done
systemctl daemon-reload || true

rm -rf "${YONDER_HOME}"
rm -rf "${YONDER_ETC}"
if [[ "${PURGE_DATA}" -eq 1 ]]; then
  rm -rf "${YONDER_DATA}" "${YONDER_LOG}"
  log "Removed data under ${YONDER_DATA} and ${YONDER_LOG}"
else
  log "Left data at ${YONDER_DATA} (pass --purge-data to remove)"
fi

log "Uninstall complete."
