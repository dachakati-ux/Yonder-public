#!/usr/bin/env bash
# Provision ai7's local model volume on the spare 960GB Intel SSD.
#
#   1x ~894GB Intel SSD -> XFS -> /srv/yonder-storage/model-bundles
#
# Selected by stable /dev/disk/by-id serial so the OS disk (sda) can never be
# targeted. Refuses mounted disks / existing signatures unless ALLOW_WIPE=1.
set -euo pipefail

SSD_DEVICE="${SSD_DEVICE:-/dev/disk/by-id/ata-INTEL_SSDSC2KG960G8_PHYG8306006E960CGN}"
STORAGE_MOUNT="/srv/yonder-storage"
MODEL_DEST="${STORAGE_MOUNT}/model-bundles"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
ALLOW_WIPE="${ALLOW_WIPE:-0}"

# ~894 GiB marketed as 960G — tolerate label vs binary sizes.
SSD_MIN_BYTES=$((800 * 1000 * 1000 * 1000))
SSD_MAX_BYTES=$((1000 * 1000 * 1000 * 1000))
PROTECTED_BASE=(sda)

log() { printf '[provision-ai7-storage] %s\n' "$*"; }
die() { printf '[provision-ai7-storage] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
command -v mkfs.xfs >/dev/null || die "xfsprogs (mkfs.xfs) not installed"

[[ -e "${SSD_DEVICE}" ]] || die "Target device missing: ${SSD_DEVICE}"
real="$(readlink -f "${SSD_DEVICE}")"
base="$(basename "${real}")"
for p in "${PROTECTED_BASE[@]}"; do
  [[ "${base}" == "${p}" ]] && die "Refusing to touch protected disk ${real}"
done
[[ "$(lsblk -dno TYPE "${real}")" == "disk" ]] || die "${real} is not a whole disk"
mnt="$(lsblk -no MOUNTPOINT "${real}" | tr -d '[:space:]')"
[[ -z "${mnt}" ]] || die "${real} (or a child) is mounted at ${mnt}"
size="$(lsblk -bdno SIZE "${real}")"
(( size >= SSD_MIN_BYTES && size <= SSD_MAX_BYTES )) \
  || die "${real} size ${size} outside expected ~960GB range"

sig="$(wipefs -n -O TYPE "${real}" 2>/dev/null | awk 'NR>1 && NF {print}' | tr '\n' ' ' | xargs || true)"
if [[ -n "${sig}" ]]; then
  if [[ "${ALLOW_WIPE}" == "1" ]]; then
    log "ALLOW_WIPE=1: clearing signatures on ${real}: ${sig}"
    while read -r part; do
      [[ -n "${part}" && "${part}" != "${real}" ]] || continue
      wipefs -a "${part}" >/dev/null 2>&1 || true
    done < <(lsblk -lnpo NAME "${real}")
    wipefs -a "${real}"
  else
    die "${real} already has a filesystem/RAID signature: ${sig} (re-run with ALLOW_WIPE=1)"
  fi
fi
log "verified raw disk ${SSD_DEVICE} -> ${real} ($((size / 1000 / 1000 / 1000)) GB)"

if blkid -o value -s TYPE "${real}" 2>/dev/null | grep -q xfs; then
  log "${real} already xfs; skipping mkfs"
else
  log "creating xfs on ${real}"
  mkfs.xfs -f "${real}"
fi

uuid="$(blkid -o value -s UUID "${real}")"
[[ -n "${uuid}" ]] || die "could not read filesystem UUID"

mkdir -p "${STORAGE_MOUNT}"
if grep -q "[[:space:]]${STORAGE_MOUNT}[[:space:]]" /etc/fstab; then
  log "fstab already references ${STORAGE_MOUNT}; leaving as-is"
else
  log "adding ${STORAGE_MOUNT} (UUID=${uuid}) to /etc/fstab"
  printf 'UUID=%s %s xfs defaults,noatime 0 2\n' "${uuid}" "${STORAGE_MOUNT}" >> /etc/fstab
fi
systemctl daemon-reload 2>/dev/null || true
mountpoint -q "${STORAGE_MOUNT}" || mount "${STORAGE_MOUNT}"

mkdir -p "${MODEL_DEST}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${STORAGE_MOUNT}"
find "${STORAGE_MOUNT}" -type d -exec chmod 0755 {} \;
command -v restorecon >/dev/null 2>&1 && restorecon -R "${STORAGE_MOUNT}" >/dev/null 2>&1 || true

log "mount:"
findmnt "${STORAGE_MOUNT}" || true
df -hT "${STORAGE_MOUNT}" | grep -vE 'tmpfs'
log "done. model root ready at ${MODEL_DEST}."
