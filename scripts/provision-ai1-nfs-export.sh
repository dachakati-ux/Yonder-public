#!/usr/bin/env bash
# Export ai1's served-model root over NFSv4 (read-only) to the lab subnet.
#
# LAB ONLY — PENDING SECURITY REVIEW / REVISION
# ---------------------------------------------
# Shares model weights inside the lab subnet. Inference still goes Gateway →
# worker. When firewalld is active this script may add NFS services to
# firewalld — that applies only if firewalld is the active host firewall.
# On YAKL-managed hosts, request NFS allows via the network owners; do not use
# this as a substitute for managed firewall policy.
#
#   /srv/yonder-storage  ->  192.0.2.0/24 (ro)
#
# Workers mount the same path so manifests keep absolute paths under
# /srv/yonder-storage/model-bundles without per-host rsync of every GGUF.
# Inference still goes Gateway → worker; this only shares weights.
#
# Usage (on ai1 as root via sudo):
#   sudo /opt/yonder/src/scripts/provision-ai1-nfs-export.sh
#
# Optional env:
#   EXPORT_PATH=/srv/yonder-storage
#   CLIENT_CIDR=192.0.2.0/24
#   INSTALL_NFS=1   # yum install nfs-utils (default 1)
set -euo pipefail

EXPORT_PATH="${EXPORT_PATH:-/srv/yonder-storage}"
CLIENT_CIDR="${CLIENT_CIDR:-192.0.2.0/24}"
INSTALL_NFS="${INSTALL_NFS:-1}"
EXPORTS_FILE="${EXPORTS_FILE:-/etc/exports}"

log() { printf '[provision-ai1-nfs-export] %s\n' "$*"; }
die() { printf '[provision-ai1-nfs-export] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
mountpoint -q "${EXPORT_PATH}" || die "${EXPORT_PATH} is not mounted"
[[ -d "${EXPORT_PATH}/model-bundles" ]] \
  || die "missing ${EXPORT_PATH}/model-bundles — refuse empty export"

if ! rpm -q nfs-utils >/dev/null 2>&1; then
  if [[ "${INSTALL_NFS}" != "1" ]]; then
    die "nfs-utils not installed (set INSTALL_NFS=1 or install manually)"
  fi
  log "installing nfs-utils"
  yum install -y --disablerepo='nvidia*' nfs-utils || \
    yum install -y nfs-utils
fi

# Read-only export. root_squash keeps worker root from writing as export root.
EXPORT_LINE="${EXPORT_PATH} ${CLIENT_CIDR}(ro,sync,no_subtree_check,root_squash)"

mkdir -p "$(dirname "${EXPORTS_FILE}")"
touch "${EXPORTS_FILE}"
if grep -qE "^[[:space:]]*${EXPORT_PATH}[[:space:]]" "${EXPORTS_FILE}"; then
  log "updating existing ${EXPORT_PATH} line in ${EXPORTS_FILE}"
  tmp="$(mktemp)"
  awk -v path="${EXPORT_PATH}" -v line="${EXPORT_LINE}" '
    $1 == path { print line; next }
    { print }
  ' "${EXPORTS_FILE}" > "${tmp}"
  mv "${tmp}" "${EXPORTS_FILE}"
else
  log "appending export to ${EXPORTS_FILE}"
  printf '%s\n' "${EXPORT_LINE}" >> "${EXPORTS_FILE}"
fi

if command -v setsebool >/dev/null 2>&1; then
  # Allow NFS to serve the storage tree under default SELinux policy.
  setsebool -P nfs_export_all_ro 1 >/dev/null 2>&1 || true
  setsebool -P use_nfs_home_dirs 1 >/dev/null 2>&1 || true
fi

systemctl enable --now rpcbind nfs-server
exportfs -ra
exportfs -v

if systemctl is-active firewalld >/dev/null 2>&1; then
  log "firewalld active: adding NFS services for ${CLIENT_CIDR} (lab/firewalld only; not a YAKL change)"
  firewall-cmd --permanent --add-service=nfs || true
  firewall-cmd --permanent --add-service=rpc-bind || true
  firewall-cmd --permanent --add-service=mountd || true
  firewall-cmd --reload || true
else
  log "firewalld not active — skip local NFS allows; on YAKL-managed hosts request allows via network owners"
fi

log "exported ${EXPORT_PATH} read-only to ${CLIENT_CIDR}"
log "next: on each worker run scripts/provision-worker-nfs-client.sh"
