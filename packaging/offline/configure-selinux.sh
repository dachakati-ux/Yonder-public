#!/usr/bin/env bash
# SELinux helpers for Yonder install paths on RHEL 8.
#
# LAB ONLY — PENDING SECURITY REVIEW / REVISION
# Applies SELinux file contexts / port labels for local install. Coordinate with
# the platform owner on managed hosts; do not treat this as a substitute for
# corporate host-hardening change control.
#
#   sudo bash configure-selinux.sh
#   sudo bash configure-selinux.sh --dry-run
set -euo pipefail

DRY_RUN=0
YONDER_HOME="${YONDER_HOME:-/opt/yonder}"
YONDER_DATA="${YONDER_DATA:-/var/lib/yonder}"
YONDER_LOG="${YONDER_LOG:-/var/log/yonder}"
YONDER_ETC="${YONDER_ETC:-/etc/yonder}"
MODEL_ROOT="${YONDER_MODEL_ROOTS:-/srv/yonder/models}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-root) MODEL_ROOT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,8p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

if ! command -v getenforce >/dev/null 2>&1; then
  echo "SELinux tools not present; skipping."
  exit 0
fi

mode="$(getenforce 2>/dev/null || echo Disabled)"
echo "SELinux mode: ${mode}"
if [[ "${mode}" == "Disabled" ]]; then
  echo "SELinux disabled; nothing to do."
  exit 0
fi

for path in "${YONDER_HOME}" "${YONDER_DATA}" "${YONDER_LOG}" "${YONDER_ETC}" "${MODEL_ROOT}"; do
  if [[ -e "${path}" ]]; then
    run restorecon -Rv "${path}" || true
  fi
done

if command -v semanage >/dev/null 2>&1; then
  # Allow non-standard ports used by Yonder services.
  run semanage port -a -t http_port_t -p tcp 8080 2>/dev/null \
    || run semanage port -m -t http_port_t -p tcp 8080 || true
  run semanage port -a -t http_port_t -p tcp 3000 2>/dev/null \
    || run semanage port -m -t http_port_t -p tcp 3000 || true
  run semanage port -a -t http_port_t -p tcp 8090 2>/dev/null \
    || run semanage port -m -t http_port_t -p tcp 8090 || true
else
  echo "semanage not installed; ran restorecon only."
  echo "If binds fail under Enforcing, install policycoreutils-python-utils"
  echo "and re-run this script, or ask the platform owner to apply the documented"
  echo "SELinux port labels as part of install:"
  echo "  semanage port -a -t http_port_t -p tcp 8080"
fi

echo "SELinux helpers finished."
