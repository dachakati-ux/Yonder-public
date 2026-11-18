#!/usr/bin/env bash
# Fetch EL8 fuse-exfat + deps for air-gapped mounts of legacy exFAT media.
# Requires Docker + internet. Output: vendor/exfat-rpms/repo/
#
#   bash packaging/offline/fetch-exfat-rpms.sh
#
# Notes:
# - Binary-compatible with RHEL 8.10 (Rocky 8 + EPEL + RPM Fusion free).
# - Use only for one-time migration off exFAT onto XFS/ext4 model volumes.
# - Do not commit *.rpm (gitignored). Re-run before offline pack builds if needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${ROOT}/vendor/exfat-rpms"

log() { printf '[fetch-exfat-rpms] %s\n' "$*"; }
die() { printf '[fetch-exfat-rpms] ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker required"

mkdir -p "${OUT}"
log "Fetching into ${OUT}"

docker run --rm --platform linux/amd64 \
  -v "${OUT}:/out" \
  rockylinux:8 \
  bash -lc '
set -euo pipefail
echo "exclude=*.i686" >> /etc/dnf/dnf.conf
dnf -y install dnf-plugins-core createrepo_c epel-release >/dev/null

# RPM Fusion free (provides fuse-exfat / exfat-utils on EL8).
dnf -y install \
  https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-8.noarch.rpm

rm -rf /out/repo /out/gpg
mkdir -p /out/repo /out/gpg

# fuse-exfat: mount.exfat-fuse (RPM Fusion). exfatprogs: mkfs/fsck (EPEL; replaces exfat-utils).
PKGS=(fuse-exfat exfatprogs fuse fuse-libs)

echo "=== download (${#PKGS[@]} pkgs + deps) ==="
dnf download --resolve --destdir=/out/repo "${PKGS[@]}"

cp -a /etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-8 /out/gpg/ 2>/dev/null || true
cp -a /etc/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-free-el-8 /out/gpg/ 2>/dev/null || true
cp -a /etc/pki/rpm-gpg/RPM-GPG-KEY-rockyofficial /out/gpg/ 2>/dev/null || true

createrepo_c /out/repo >/dev/null
rpm -qp --qf "%{NAME}\n" /out/repo/*.rpm 2>/dev/null | sort -u > /out/package-names.txt
cat > /out/README.md <<EOF
# EL8 exFAT FUSE RPMs (offline)

Fetched from Rocky Linux 8 + EPEL + RPM Fusion free for air-gapped mounts of
legacy exFAT media on RHEL 8.10.

* \`repo/\` — local dnf repository (RPMs + repodata)
* Install on the host with \`bash packaging/offline/enable-exfat.sh --rpm-dir <path>\`
* One-time use: mount exFAT → copy onto XFS/ext4 → unmount. Do not keep
  production model roots on exFAT.

Do not commit \`*.rpm\` (gitignored). Re-run \`bash packaging/offline/fetch-exfat-rpms.sh\`.
EOF

echo "=== summary ==="
rpm_count=$(ls -1 /out/repo/*.rpm 2>/dev/null | wc -l)
echo "rpm_count=${rpm_count}"
du -sh /out/repo
ls -1 /out/repo/*.rpm
'

log "Done. Next: bash packaging/offline/enable-exfat.sh --rpm-dir vendor/exfat-rpms/repo"
