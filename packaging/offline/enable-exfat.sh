#!/usr/bin/env bash
# Install offline fuse-exfat on RHEL 8 and optionally mount an exFAT device.
#
#   sudo bash packaging/offline/enable-exfat.sh --rpm-dir /path/to/repo
#   sudo bash packaging/offline/enable-exfat.sh --rpm-dir /path/to/repo \
#     --device /dev/sdc2 --mountpoint /mnt/yonder-src
#
# Prefer XFS/ext4 for permanent model volumes. This script is for one-time
# migration off legacy exFAT media when the host has no internet.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

RPM_DIR=""
DEVICE=""
MOUNTPOINT="/mnt/yonder-src"
REPO_NAME="yonder-offline-exfat"

usage() {
  cat <<EOF
Usage: sudo bash $0 --rpm-dir DIR [--device DEV] [--mountpoint PATH]

  --rpm-dir DIR       Directory of fuse-exfat RPMs + createrepo metadata
  --device DEV        Optional exFAT partition (e.g. /dev/sdc2)
  --mountpoint PATH   Mount point when --device is set (default: /mnt/yonder-src)
EOF
}

while [[ $# -gt 0 ]]; do
  # Tolerate stray whitespace/backslashes from pasted line continuations.
  arg="${1#"${1%%[![:space:]\\]*}"}"
  arg="${arg%"${arg##*[![:space:]\\]}"}"
  case "${arg}" in
    "")
      shift
      ;;
    --rpm-dir)
      RPM_DIR="${2:-}"
      shift 2
      ;;
    --device)
      DEVICE="${2:-}"
      shift 2
      ;;
    --mountpoint)
      MOUNTPOINT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "Unknown argument: '$1'"
      ;;
  esac
done

RPM_DIR="${RPM_DIR//[[:space:]]/}"
DEVICE="${DEVICE//[[:space:]]/}"
MOUNTPOINT="${MOUNTPOINT//[[:space:]]/}"

require_root
assert_rhel_like
assert_x86_64

[[ -n "${RPM_DIR}" ]] || die "--rpm-dir is required"
[[ -d "${RPM_DIR}" ]] || die "RPM dir not found: ${RPM_DIR}"
[[ -f "${RPM_DIR}/repodata/repomd.xml" ]] || die "Missing createrepo metadata at ${RPM_DIR}/repodata (run fetch-exfat-rpms.sh)"

RPM_DIR="$(cd "${RPM_DIR}" && pwd)"

mkdir -p /etc/yum.repos.d
cat > "/etc/yum.repos.d/${REPO_NAME}.repo" <<EOF
[${REPO_NAME}]
name=Yonder offline exFAT FUSE RPMs (EL8)
baseurl=file://${RPM_DIR}
enabled=1
gpgcheck=0
module_hotfixes=1
EOF

PKGS=(fuse-exfat exfatprogs fuse)
log "Installing ${PKGS[*]} from ${RPM_DIR}"
if command -v dnf >/dev/null; then
  dnf -y install \
    --disablerepo='*' \
    --enablerepo="${REPO_NAME}" \
    --setopt="${REPO_NAME}.gpgcheck=0" \
    --nogpgcheck \
    "${PKGS[@]}" \
    || die "Offline fuse-exfat install failed"
elif command -v yum >/dev/null; then
  yum -y install \
    --disablerepo='*' \
    --enablerepo="${REPO_NAME}" \
    --nogpgcheck \
    "${PKGS[@]}" \
    || die "Offline fuse-exfat install failed"
else
  die "dnf/yum required"
fi

if ! command -v mount.exfat-fuse >/dev/null && ! command -v mount.exfat >/dev/null; then
  die "fuse-exfat installed but mount.exfat-fuse not on PATH"
fi

log "exFAT FUSE ready: $(command -v mount.exfat-fuse 2>/dev/null || command -v mount.exfat)"

if [[ -n "${DEVICE}" ]]; then
  [[ -b "${DEVICE}" ]] || die "Not a block device: ${DEVICE}"
  mkdir -p "${MOUNTPOINT}"
  if mountpoint -q "${MOUNTPOINT}"; then
    log "${MOUNTPOINT} already mounted"
  else
    if command -v mount.exfat-fuse >/dev/null; then
      mount.exfat-fuse "${DEVICE}" "${MOUNTPOINT}" \
        || die "mount.exfat-fuse failed for ${DEVICE}"
    else
      mount -t exfat "${DEVICE}" "${MOUNTPOINT}" \
        || die "mount -t exfat failed for ${DEVICE}"
    fi
    log "Mounted ${DEVICE} at ${MOUNTPOINT}"
  fi
  ls -la "${MOUNTPOINT}" | head -20
fi

log "Done. After copying data onto an XFS/ext4 volume, unmount and remove the exFAT media."
