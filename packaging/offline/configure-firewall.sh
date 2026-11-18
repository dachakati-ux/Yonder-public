#!/usr/bin/env bash
# Configure firewalld for a Yonder lab host (RHEL 8).
#
# LAB ONLY — PENDING SECURITY REVIEW / REVISION
# ---------------------------------------------
# This helper mutates **firewalld** on hosts where firewalld is the active local
# firewall. It is NOT a YAKL change and must NOT be used to circumvent or replace
# managed host firewall policy.
#
# On YAKL-managed / corporate-firewalled hosts: do not run this script to gain
# access. File a firewall change request with the network/security owners
# (see docs/security/jira-firewall-lab-console-access.md).
#
#   sudo bash configure-firewall.sh --role control-plane
#   sudo bash configure-firewall.sh --role worker
#   sudo bash configure-firewall.sh --role control-plane --dry-run
set -euo pipefail

ROLE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -n "${ROLE}" ]] || { echo "--role control-plane|worker required" >&2; exit 1; }

run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

if ! command -v firewall-cmd >/dev/null 2>&1; then
  echo "firewall-cmd not found." >&2
  echo "Lab/firewalld helper only. On YAKL-managed hosts, coordinate port allows" >&2
  echo "with the host firewall owner (do not self-open managed policy)." >&2
  exit 0
fi

if ! firewall-cmd --state >/dev/null 2>&1; then
  echo "firewalld is not running." >&2
  echo "Lab/firewalld helper only. On YAKL-managed hosts, coordinate port allows" >&2
  echo "with the host firewall owner (do not self-open managed policy)." >&2
  exit 0
fi

case "${ROLE}" in
  control-plane)
    run firewall-cmd --permanent --add-port=8080/tcp
    run firewall-cmd --permanent --add-port=3000/tcp
    ;;
  worker)
    # Gateway → worker lifecycle callbacks
    run firewall-cmd --permanent --add-port=8090/tcp
    ;;
  *)
    echo "Unknown role: ${ROLE} (expected control-plane|worker)" >&2
    exit 1
    ;;
esac

run firewall-cmd --reload
echo "firewalld rules applied for role=${ROLE} (lab helper; not a YAKL change)"
