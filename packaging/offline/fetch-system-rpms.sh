#!/usr/bin/env bash
# Fetch RHEL 8-compatible system RPMs (Rocky Linux 8) for air-gapped installs.
# Requires Docker + internet. Output: vendor/rhel8-rpms/repo/ (RPMs + createrepo metadata).
#
#   bash packaging/offline/fetch-system-rpms.sh
#
# Notes:
# - Packages are binary-compatible with RHEL 8.10; use Red Hat media/reposync instead
#   if corporate policy forbids Rocky RPMs on RHEL.
# - kernel-devel is pinned to whatever Rocky currently ships; GPU hosts whose running
#   kernel does not match must add matching kernel-devel from RHEL install media.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${ROOT}/vendor/rhel8-rpms"
NVIDIA_RPM=""
for candidate in \
  "${ROOT}/vendor/nvidia/"nvidia-*.rpm \
  "${ROOT}/"nvidia-*.rpm
do
  if [[ -f "${candidate}" ]]; then
    NVIDIA_RPM="${candidate}"
    break
  fi
done

log() { printf '[fetch-system-rpms] %s\n' "$*"; }
die() { printf '[fetch-system-rpms] ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker required"
[[ -f "${ROOT}/packaging/offline/system-packages.base.txt" ]] || die "missing system-packages.base.txt"
[[ -n "${NVIDIA_RPM}" ]] || die "Place nvidia-*.rpm (local-repo) at repo root or vendor/nvidia/ first"

mkdir -p "${OUT}"
log "Fetching into ${OUT} using $(basename "${NVIDIA_RPM}")"

docker run --rm --platform linux/amd64 \
  -v "${ROOT}/packaging/offline:/lists:ro" \
  -v "${NVIDIA_RPM}:/n.rpm:ro" \
  -v "${OUT}:/out" \
  rockylinux:8 \
  bash -lc '
set -euo pipefail
echo "exclude=*.i686" >> /etc/dnf/dnf.conf
dnf -y install dnf-plugins-core createrepo_c epel-release >/dev/null
rpm -ivh /n.rpm
rpm --import /var/nvidia-driver-local-repo-rhel8-580.126.20/3183BF0F.pub 2>/dev/null \
  || rpm --import /var/nvidia-driver-local-repo-*/3183BF0F.pub

rm -rf /out/base /out/nvidia-build /out/nvidia-os-deps /out/repo /out/epel-gpg
mkdir -p /out/base /out/nvidia-build /out/nvidia-os-deps /out/repo /out/epel-gpg

mapfile -t BASE_PKGS < <(grep -vE "^[[:space:]]*(#|$)" /lists/system-packages.base.txt)
mapfile -t NVIDIA_BUILD < <(grep -vE "^[[:space:]]*(#|$)" /lists/system-packages.nvidia.txt)

echo "=== base (${#BASE_PKGS[@]} pkgs) ==="
dnf download --resolve --destdir=/out/base "${BASE_PKGS[@]}"

echo "=== nvidia-build (${#NVIDIA_BUILD[@]} pkgs) ==="
dnf download --resolve --destdir=/out/nvidia-build "${NVIDIA_BUILD[@]}"

echo "=== cuda-drivers OS dependency closure ==="
dnf -y module enable nvidia-driver:580-dkms
dnf -y install --downloadonly --downloaddir=/out/nvidia-os-deps cuda-drivers

cp -n /out/base/*.rpm /out/repo/ || true
cp -n /out/nvidia-build/*.rpm /out/repo/ || true
for f in /out/nvidia-os-deps/*.rpm; do
  [[ -f "$f" ]] || continue
  bn=$(basename "$f")
  case "$bn" in
    nvidia-*|cuda-*|kmod-nvidia*|libnvidia*|egl-*|xorg-x11-nvidia*|dnf-plugin-nvidia*|libvdpau*|vdpauinfo*|collectx*|mft-*|nvl*|nvlink*|eglexternalplatform*)
      continue
      ;;
  esac
  cp -n "$f" /out/repo/ || true
done

cp -a /etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-8 /out/epel-gpg/ 2>/dev/null || true
cp -a /etc/pki/rpm-gpg/RPM-GPG-KEY-rockyofficial /out/ 2>/dev/null || true

createrepo_c /out/repo >/dev/null
rpm -qp --qf "%{NAME}\n" /out/repo/*.rpm 2>/dev/null | sort -u > /out/package-names.txt
cat > /out/README.md <<EOF
# RHEL 8 system RPMs (offline)

Fetched from Rocky Linux 8 (+ EPEL for dkms) for air-gapped Yonder installs.

* \`repo/\` — local dnf repository (RPMs + repodata) copied into the USB pack
* Do not commit \`*.rpm\` (gitignored). Re-run \`bash packaging/offline/fetch-system-rpms.sh\` before \`make offline-pack\`.

kernel-devel in this tree must match the GPU host kernel (\`uname -r\`) for DKMS.
EOF

echo "=== summary ==="
echo rpm_count=$(find /out/repo -maxdepth 1 -name "*.rpm" | wc -l)
du -sh /out/repo
'

log "Done. Next: make offline-pack"
