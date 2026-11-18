#!/usr/bin/env bash
# Install bundled NVIDIA driver RPMs only (from payload/nvidia/).
# Useful on the GPU worker if you skipped drivers during install-worker.sh.
#
#   sudo bash install-nvidia.sh
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${PACK_ROOT}/payload/scripts/lib.sh"

require_root
assert_x86_64
assert_rhel_like
PACK_ROOT="$(detect_pack_root)"
# Installs offline system RPMs (gcc/dkms/kernel-devel/…) then NVIDIA local-repo + cuda-drivers.
install_nvidia_driver_rpms "${PACK_ROOT}"
log "Done. Reboot if nvidia-smi is not working yet."
