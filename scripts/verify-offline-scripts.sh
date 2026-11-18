#!/usr/bin/env bash
# Syntax-check offline packaging scripts (no Docker / root required).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS=(
  packaging/offline/build-usb-pack.sh
  packaging/offline/fetch-system-rpms.sh
  packaging/offline/fetch-exfat-rpms.sh
  packaging/offline/enable-exfat.sh
  packaging/offline/install.sh
  packaging/offline/install-worker.sh
  packaging/offline/install-nvidia.sh
  packaging/offline/install-llama-runtime.sh
  packaging/offline/uninstall.sh
  packaging/offline/lib.sh
  packaging/offline/verify-install.sh
  packaging/offline/verify-runtime-artifacts.sh
  packaging/offline/configure-firewall.sh
  packaging/offline/configure-selinux.sh
)

fail=0
for script in "${SCRIPTS[@]}"; do
  path="${ROOT}/${script}"
  if [[ ! -f "${path}" ]]; then
    printf '[missing] %s\n' "${script}" >&2
    fail=1
    continue
  fi
  if bash -n "${path}"; then
    printf '[ok] bash -n %s\n' "${script}"
  else
    printf '[FAIL] bash -n %s\n' "${script}" >&2
    fail=1
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  exit 1
fi

assert_contains() {
  local file="$1"
  local pattern="$2"
  local description="$3"
  if grep -qF "${pattern}" "${ROOT}/${file}"; then
    printf '[ok] %s\n' "${description}"
  else
    printf '[FAIL] %s (%s)\n' "${description}" "${file}" >&2
    fail=1
  fi
}

assert_contains packaging/offline/install.sh \
  '"YONDER_MODEL_ROOTS=${MODEL_ROOT}"' \
  "control-plane exports configured model root"
assert_contains packaging/offline/install.sh \
  '"YONDER_REPO_ROOT=${YONDER_HOME}/repo"' \
  "control-plane exports installed repo root"
assert_contains packaging/offline/install.sh \
  "yonder-gateway.service.d/model-root.conf" \
  "gateway systemd sandbox permits configured model root"
assert_contains packaging/offline/install.sh \
  "RHEL 8 does not support exFAT" \
  "installer rejects unsupported exFAT model storage"
assert_contains packaging/offline/install.sh \
  "Importing staged model bundles" \
  "installer imports staged model bundles"
assert_contains packaging/offline/enable-exfat.sh \
  "mount.exfat-fuse" \
  "enable-exfat installs FUSE mount helper"
assert_contains packaging/offline/fetch-exfat-rpms.sh \
  "fuse-exfat" \
  "fetch-exfat targets fuse-exfat"

if [[ "${fail}" -ne 0 ]]; then
  exit 1
fi
echo "Offline packaging scripts syntax OK"
