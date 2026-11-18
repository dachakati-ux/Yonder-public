#!/usr/bin/env bash
# Mount ai1's served-model root over NFSv4 at /srv/yonder-storage on a worker.
#
# Preserves any existing local XFS volume at /srv/yonder-storage.local so
# operators can fall back or keep scratch/derived work offline from NFS.
#
# Usage (on ai3 or ai7 as root):
#   sudo CONFIRM=1 /opt/yonder/src/scripts/provision-worker-nfs-client.sh
#
# Optional env:
#   NFS_SERVER=control-plane.example.internal
#   EXPORT_PATH=/srv/yonder-storage
#   MOUNT_PATH=/srv/yonder-storage
#   LOCAL_BACKUP=/srv/yonder-storage.local
#   INSTALL_NFS=1
#   STOP_WORKER=1   # systemctl stop yonder-worker during cutover (default 1)
#   CONFIRM=1       # required safety gate
set -euo pipefail

NFS_SERVER="${NFS_SERVER:-control-plane.example.internal}"
EXPORT_PATH="${EXPORT_PATH:-/srv/yonder-storage}"
MOUNT_PATH="${MOUNT_PATH:-/srv/yonder-storage}"
LOCAL_BACKUP="${LOCAL_BACKUP:-/srv/yonder-storage.local}"
INSTALL_NFS="${INSTALL_NFS:-1}"
STOP_WORKER="${STOP_WORKER:-1}"
CONFIRM="${CONFIRM:-0}"
FSTAB_MARKER="# yonder-nfs-model-root"

log() { printf '[provision-worker-nfs-client] %s\n' "$*"; }
die() { printf '[provision-worker-nfs-client] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
[[ "${CONFIRM}" == "1" ]] || die "Refusing without CONFIRM=1"

if ! rpm -q nfs-utils >/dev/null 2>&1; then
  if [[ "${INSTALL_NFS}" != "1" ]]; then
    die "nfs-utils not installed (set INSTALL_NFS=1 or install manually)"
  fi
  log "installing nfs-utils"
  yum install -y --disablerepo='nvidia*' nfs-utils || \
    yum install -y nfs-utils
fi

systemctl enable --now rpcbind

log "probing ${NFS_SERVER}:${EXPORT_PATH}"
showmount -e "${NFS_SERVER}" >/dev/null \
  || die "cannot list exports on ${NFS_SERVER} (export/firewall/nfs-server?)"

worker_was_active=0
if [[ "${STOP_WORKER}" == "1" ]] && systemctl is-active yonder-worker >/dev/null 2>&1; then
  worker_was_active=1
  log "stopping yonder-worker for storage cutover"
  systemctl stop yonder-worker
fi

# If the current mount is already the NFS share, refresh and exit.
if mountpoint -q "${MOUNT_PATH}"; then
  src="$(findmnt -no SOURCE "${MOUNT_PATH}" || true)"
  fstype="$(findmnt -no FSTYPE "${MOUNT_PATH}" || true)"
  if [[ "${fstype}" == nfs* ]]; then
    log "${MOUNT_PATH} already NFS (${src}); remounting"
    mount -o remount "${MOUNT_PATH}"
    if [[ "${worker_was_active}" -eq 1 ]]; then
      systemctl start yonder-worker
    fi
    log "done"
    exit 0
  fi
fi

# Move a local XFS (or other) volume aside so the NFS path can take its place.
if mountpoint -q "${MOUNT_PATH}"; then
  fstype="$(findmnt -no FSTYPE "${MOUNT_PATH}" || true)"
  if [[ "${fstype}" != nfs* ]]; then
    if mountpoint -q "${LOCAL_BACKUP}"; then
      die "${LOCAL_BACKUP} already mounted — resolve manually before re-run"
    fi
    mkdir -p "${LOCAL_BACKUP}"
    uuid="$(findmnt -no UUID "${MOUNT_PATH}" || true)"
    source_dev="$(findmnt -no SOURCE "${MOUNT_PATH}" || true)"
    [[ -n "${uuid}" || -n "${source_dev}" ]] \
      || die "could not resolve local volume identity for ${MOUNT_PATH}"
    log "relocating local ${MOUNT_PATH} (${fstype}) -> ${LOCAL_BACKUP}"
    if grep -qE "[[:space:]]${MOUNT_PATH}[[:space:]]" /etc/fstab; then
      tmp="$(mktemp)"
      awk -v old="${MOUNT_PATH}" -v neu="${LOCAL_BACKUP}" '
        $1 !~ /^#/ && $2 == old {
          $2 = neu
          print
          next
        }
        { print }
      ' /etc/fstab > "${tmp}"
      mv "${tmp}" /etc/fstab
      systemctl daemon-reload 2>/dev/null || true
    elif [[ -n "${uuid}" ]] && ! grep -qE "[[:space:]]${LOCAL_BACKUP}[[:space:]]" /etc/fstab; then
      printf 'UUID=%s %s xfs defaults,noatime 0 2\n' "${uuid}" "${LOCAL_BACKUP}" >> /etc/fstab
      systemctl daemon-reload 2>/dev/null || true
    fi
    # Prefer umount+remount: mount --move fails when the parent is shared.
    umount "${MOUNT_PATH}" || die "umount ${MOUNT_PATH} failed"
    if [[ -n "${uuid}" ]]; then
      mount -U "${uuid}" "${LOCAL_BACKUP}" \
        || die "could not mount UUID=${uuid} at ${LOCAL_BACKUP}"
    else
      mount "${source_dev}" "${LOCAL_BACKUP}" \
        || die "could not mount ${source_dev} at ${LOCAL_BACKUP}"
    fi
    mountpoint -q "${LOCAL_BACKUP}" || die "local backup not mounted at ${LOCAL_BACKUP}"
    log "local volume available at ${LOCAL_BACKUP}"
  fi
fi

mkdir -p "${MOUNT_PATH}"

NFS_OPTS="ro,nolock,hard,timeo=600,retrans=2,_netdev,nconnect=4"
# Mount the absolute export path (do not use fsid=0 / NFSv4 pseudo-root).
if ! grep -qF "${FSTAB_MARKER}" /etc/fstab; then
  log "adding NFS fstab entry"
  printf '%s\n%s:%s %s nfs4 %s 0 0\n' \
    "${FSTAB_MARKER}" \
    "${NFS_SERVER}" "${EXPORT_PATH}" "${MOUNT_PATH}" "${NFS_OPTS}" \
    >> /etc/fstab
  systemctl daemon-reload 2>/dev/null || true
fi

log "mounting ${NFS_SERVER}:${EXPORT_PATH} -> ${MOUNT_PATH}"
mount "${MOUNT_PATH}" || mount -t nfs4 -o "${NFS_OPTS}" \
  "${NFS_SERVER}:${EXPORT_PATH}" "${MOUNT_PATH}"

mountpoint -q "${MOUNT_PATH}" || die "NFS mount failed"
[[ -d "${MOUNT_PATH}/model-bundles" ]] \
  || die "mounted tree missing model-bundles — wrong export?"

# Spot-check a known served path if present on the server catalog.
if [[ -e "${MOUNT_PATH}/model-bundles/gemma-4-31B-it-qat-gguf" ]]; then
  log "verified gemma-4-31B-it-qat-gguf visible via NFS"
fi

findmnt "${MOUNT_PATH}"
df -h "${MOUNT_PATH}"

if [[ "${worker_was_active}" -eq 1 ]]; then
  log "starting yonder-worker"
  systemctl start yonder-worker
fi

log "NFS model root ready at ${MOUNT_PATH} (local backup: ${LOCAL_BACKUP})"
log "load timing is recorded by gateway/worker telemetry — watch deployment logs for duration_ms"
