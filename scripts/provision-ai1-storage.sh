#!/usr/bin/env bash
# Provision ai1 model + archive storage tiers from the newly added disks.
#
#   SSD tier : 4x 1.2TB Intel SSD -> mdadm RAID10 -> /srv/yonder-storage
#              (fast, redundant served-model root; manifest paths unchanged)
#   HDD tier : 4x 16TB Seagate    -> mdadm RAID6  -> /srv/yonder-archive
#              (~32 TB usable, survives 2 disk failures; bulk originals,
#               derived GGUF, offline pack, artifact history)
#
# Disks are selected by stable /dev/disk/by-id serials so the OS disk (sda),
# the exFAT staging disk (sdb), and the current model SSD (sdc) can never be
# targeted. Every target is refused unless it is an unmounted whole disk.
# Leftover DOS/partition signatures on otherwise empty disks require an
# explicit ALLOW_WIPE=1. The existing 460G catalog on sdc is copied to the
# new SSD array and LEFT IN PLACE as a rollback; nothing on sdc is erased.
#
# Usage:
#   sudo ALLOW_WIPE=1 /opt/yonder/src/scripts/provision-ai1-storage.sh
set -euo pipefail

# --- target devices (override via env if serials ever change) ---------------
SSD_DEVICES=(
  "${SSD0:-/dev/disk/by-id/ata-INTEL_SSDSC2BX012T4_BTHC709102R11P2OGN}"
  "${SSD1:-/dev/disk/by-id/ata-INTEL_SSDSC2BX012T4_BTHC709200FG1P2OGN}"
  "${SSD2:-/dev/disk/by-id/ata-INTEL_SSDSC2BX012T4_BTHC709200ER1P2OGN}"
  "${SSD3:-/dev/disk/by-id/ata-INTEL_SSDSC2BX012T4_BTHC7091018W1P2OGN}"
)
HDD_DEVICES=(
  "${HDD0:-/dev/disk/by-id/ata-ST16000NM001G-2KK103_ZL201H15}"
  "${HDD1:-/dev/disk/by-id/ata-ST16000NM001G-2KK103_ZL201GCD}"
  "${HDD2:-/dev/disk/by-id/ata-ST16000NM001G-2KK103_ZL201C73}"
  "${HDD3:-/dev/disk/by-id/ata-ST16000NM001G-2KK103_ZL2019RK}"
)

SSD_ARRAY="/dev/md/yonder-ssd"
HDD_ARRAY="/dev/md/yonder-archive"
STORAGE_MOUNT="/srv/yonder-storage"
ARCHIVE_MOUNT="/srv/yonder-archive"
OLD_STORAGE_MOUNT="/srv/yonder-storage.old"
STAGE_MOUNT="/mnt/yonder-ssd-new"

SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
MIGRATE_CATALOG="${MIGRATE_CATALOG:-1}"
ALLOW_WIPE="${ALLOW_WIPE:-0}"

# Approximate raw byte sizes with generous tolerance for label vs binary sizes.
SSD_MIN_BYTES=$((1000 * 1000 * 1000 * 1000))   # >= ~1.0 TB
SSD_MAX_BYTES=$((1400 * 1000 * 1000 * 1000))   # <= ~1.4 TB
HDD_MIN_BYTES=$((14 * 1000 * 1000 * 1000 * 1000))  # >= ~14 TB
HDD_MAX_BYTES=$((18 * 1000 * 1000 * 1000 * 1000))  # <= ~18 TB

log() { printf '[provision-ai1-storage] %s\n' "$*"; }
die() { printf '[provision-ai1-storage] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
command -v mdadm >/dev/null || die "mdadm not installed"
command -v mkfs.xfs >/dev/null || die "xfsprogs (mkfs.xfs) not installed"
command -v rsync >/dev/null || die "rsync not installed"

# Refuse to run against the protected disks even if a serial is mis-set.
PROTECTED_BASE=(sda sdb sdc)

verify_raw_disk() {
  local devlink="$1" min="$2" max="$3" real base type mnt sig pv size
  [[ -e "${devlink}" ]] || die "Target device missing: ${devlink}"
  real="$(readlink -f "${devlink}")"
  base="$(basename "${real}")"
  for p in "${PROTECTED_BASE[@]}"; do
    [[ "${base}" == "${p}" ]] && die "Refusing to touch protected disk ${real} (${devlink})"
  done
  type="$(lsblk -dno TYPE "${real}")"
  [[ "${type}" == "disk" ]] || die "${real} is not a whole disk (type=${type})"
  mnt="$(lsblk -no MOUNTPOINT "${real}" | tr -d '[:space:]')"
  [[ -z "${mnt}" ]] || die "${real} (or a child) is mounted at ${mnt}"
  if command -v pvs >/dev/null 2>&1 && pvs --noheadings -o pv_name 2>/dev/null | grep -qw "${real}"; then
    die "${real} is an LVM physical volume"
  fi
  size="$(lsblk -bdno SIZE "${real}")"
  (( size >= min && size <= max )) || die "${real} size ${size} outside expected range [${min},${max}]"

  # Collect wipefs TYPE column (skip header). Empty disks often still carry a
  # leftover DOS/MBR label from prior use — clear only with ALLOW_WIPE=1.
  sig="$(wipefs -n -O TYPE "${real}" 2>/dev/null | awk 'NR>1 && NF {print}' | tr '\n' ' ' | xargs || true)"
  if [[ -n "${sig}" ]]; then
    if [[ "${ALLOW_WIPE}" == "1" ]]; then
      log "ALLOW_WIPE=1: clearing signatures on ${real}: ${sig}"
      # Wipe any child partitions first, then the whole disk.
      while read -r part; do
        [[ -n "${part}" && "${part}" != "${real}" ]] || continue
        wipefs -a "${part}" >/dev/null 2>&1 || true
      done < <(lsblk -lnpo NAME "${real}")
      wipefs -a "${real}"
    else
      die "${real} already has a filesystem/RAID signature: ${sig} (re-run with ALLOW_WIPE=1 to clear leftover labels on empty disks)"
    fi
  fi
  log "verified raw disk ${devlink} -> ${real} ($((size / 1000 / 1000 / 1000)) GB)"
}

create_array() {
  local array="$1" level="$2"; shift 2
  local devs=("$@") real_devs=()
  if [[ -e "${array}" ]]; then
    log "array ${array} already exists; skipping create"
    return 0
  fi
  for d in "${devs[@]}"; do real_devs+=("$(readlink -f "${d}")"); done
  log "creating ${array} (RAID${level}) from: ${real_devs[*]}"
  mdadm --create "${array}" \
    --level="${level}" \
    --raid-devices="${#real_devs[@]}" \
    --homehost="$(hostname -s)" \
    --run \
    --metadata=1.2 \
    "${real_devs[@]}"
}

make_xfs_if_needed() {
  local array="$1"
  if blkid -o value -s TYPE "${array}" 2>/dev/null | grep -q xfs; then
    log "${array} already has an xfs filesystem; skipping mkfs"
  else
    log "creating xfs on ${array}"
    mkfs.xfs -f "${array}"
  fi
}

ensure_fstab() {
  local uuid="$1" mount="$2"
  mkdir -p "${mount}"
  if grep -q "[[:space:]]${mount}[[:space:]]" /etc/fstab; then
    log "fstab already references ${mount}; leaving as-is"
  else
    log "adding ${mount} (UUID=${uuid}) to /etc/fstab"
    printf 'UUID=%s %s xfs defaults,noatime 0 2\n' "${uuid}" "${mount}" >> /etc/fstab
  fi
}

# --- phase 1: verify every target is safe before doing anything -------------
log "verifying SSD targets"
for d in "${SSD_DEVICES[@]}"; do verify_raw_disk "${d}" "${SSD_MIN_BYTES}" "${SSD_MAX_BYTES}"; done
log "verifying HDD targets"
for d in "${HDD_DEVICES[@]}"; do verify_raw_disk "${d}" "${HDD_MIN_BYTES}" "${HDD_MAX_BYTES}"; done

# --- phase 2: build arrays + filesystems ------------------------------------
create_array "${HDD_ARRAY}" 6 "${HDD_DEVICES[@]}"
create_array "${SSD_ARRAY}" 10 "${SSD_DEVICES[@]}"
make_xfs_if_needed "${HDD_ARRAY}"
make_xfs_if_needed "${SSD_ARRAY}"

log "persisting array assembly for boot"
mdadm --detail --scan | while read -r line; do
  arrname="$(awk '{print $2}' <<<"${line}")"
  grep -q "${arrname}" /etc/mdadm.conf 2>/dev/null || printf '%s\n' "${line}" >> /etc/mdadm.conf
done
if command -v dracut >/dev/null 2>&1; then
  dracut -f >/dev/null 2>&1 || log "WARN: dracut regeneration returned non-zero"
fi

SSD_UUID="$(blkid -o value -s UUID "${SSD_ARRAY}")"
HDD_UUID="$(blkid -o value -s UUID "${HDD_ARRAY}")"
[[ -n "${SSD_UUID}" ]] || die "could not read SSD array UUID"
[[ -n "${HDD_UUID}" ]] || die "could not read HDD array UUID"

# --- phase 3: archive tier --------------------------------------------------
ensure_fstab "${HDD_UUID}" "${ARCHIVE_MOUNT}"
mountpoint -q "${ARCHIVE_MOUNT}" || mount "${ARCHIVE_MOUNT}"
mkdir -p "${ARCHIVE_MOUNT}/originals" "${ARCHIVE_MOUNT}/offline-pack" \
  "${ARCHIVE_MOUNT}/derived" "${ARCHIVE_MOUNT}/artifacts"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${ARCHIVE_MOUNT}"
command -v restorecon >/dev/null 2>&1 && restorecon -R "${ARCHIVE_MOUNT}" >/dev/null 2>&1 || true
log "archive tier ready at ${ARCHIVE_MOUNT}"

# --- phase 4: migrate catalog onto the SSD array ----------------------------
if [[ "${MIGRATE_CATALOG}" == "1" ]] && mountpoint -q "${STORAGE_MOUNT}"; then
  log "migrating existing catalog onto the SSD array"
  mkdir -p "${STAGE_MOUNT}"
  mountpoint -q "${STAGE_MOUNT}" || mount "${SSD_ARRAY}" "${STAGE_MOUNT}"

  log "stopping gateway + worker to quiesce the model root"
  systemctl stop yonder-gateway 2>/dev/null || true
  systemctl stop yonder-worker 2>/dev/null || true

  log "rsync ${STORAGE_MOUNT}/ -> ${STAGE_MOUNT}/ (this can take a while)"
  rsync -aHAX --numeric-ids --info=progress2 "${STORAGE_MOUNT}/" "${STAGE_MOUNT}/"

  src_files="$(find "${STORAGE_MOUNT}" -type f | wc -l)"
  dst_files="$(find "${STAGE_MOUNT}" -type f | wc -l)"
  log "file count: source=${src_files} copy=${dst_files}"
  (( dst_files >= src_files )) || die "copy is missing files; aborting swap (old catalog untouched)"

  umount "${STAGE_MOUNT}"
  # Managed llama-server children can outlive systemctl stop and keep the
  # model root busy; clear them before unmounting the old volume.
  pkill -TERM -u "${SERVICE_USER}" -x llama-server 2>/dev/null || true
  sleep 2
  pkill -KILL -u "${SERVICE_USER}" -x llama-server 2>/dev/null || true
  fuser -km "${STORAGE_MOUNT}" 2>/dev/null || true
  sleep 1
  umount "${STORAGE_MOUNT}"

  log "repointing fstab: SSD array -> ${STORAGE_MOUNT}, old sdc -> ${OLD_STORAGE_MOUNT}"
  # Move the current sdc line to the rollback mountpoint, then mount the array.
  # Use a temp file + cat (never interactive mv -i) so root aliases cannot stall.
  fstab_tmp="$(mktemp)"
  sed "s#[[:space:]]${STORAGE_MOUNT}[[:space:]]# ${OLD_STORAGE_MOUNT} #" /etc/fstab > "${fstab_tmp}"
  cat "${fstab_tmp}" > /etc/fstab
  rm -f "${fstab_tmp}"
  mkdir -p "${OLD_STORAGE_MOUNT}"
  ensure_fstab "${SSD_UUID}" "${STORAGE_MOUNT}"
  systemctl daemon-reload 2>/dev/null || true

  mount "${STORAGE_MOUNT}"
  mount "${OLD_STORAGE_MOUNT}" 2>/dev/null || log "note: mount old sdc later with 'mount ${OLD_STORAGE_MOUNT}'"

  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${STORAGE_MOUNT}"
  command -v restorecon >/dev/null 2>&1 && restorecon -R "${STORAGE_MOUNT}" >/dev/null 2>&1 || true

  log "restarting gateway + worker"
  systemctl start yonder-gateway 2>/dev/null || true
  systemctl start yonder-worker 2>/dev/null || true
else
  log "skipping catalog migration; mounting SSD array at ${STORAGE_MOUNT}"
  ensure_fstab "${SSD_UUID}" "${STORAGE_MOUNT}"
  mountpoint -q "${STORAGE_MOUNT}" || mount "${STORAGE_MOUNT}"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${STORAGE_MOUNT}"
fi

# --- summary ----------------------------------------------------------------
log "current RAID status:"
cat /proc/mdstat
log "mounts:"
findmnt "${STORAGE_MOUNT}" || true
findmnt "${ARCHIVE_MOUNT}" || true
df -hT "${STORAGE_MOUNT}" "${ARCHIVE_MOUNT}" | grep -vE 'tmpfs'
log "done. RAID arrays may still be resyncing in the background (see /proc/mdstat);"
log "the filesystems are usable now. Old catalog preserved read-only rollback at ${OLD_STORAGE_MOUNT}."
