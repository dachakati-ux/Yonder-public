#!/usr/bin/env bash
# Shared helpers for offline installers (sourced by install*.sh).
# shellcheck shell=bash

YONDER_HOME="${YONDER_HOME:-/opt/yonder}"
YONDER_ETC="${YONDER_ETC:-/etc/yonder}"
YONDER_DATA="${YONDER_DATA:-/var/lib/yonder}"
YONDER_LOG="${YONDER_LOG:-/var/log/yonder}"
YONDER_USER="${YONDER_USER:-yonder}"
YONDER_GROUP="${YONDER_GROUP:-yonder}"

log() { printf '[yonder-install] %s\n' "$*"; }
warn() { printf '[yonder-install] WARNING: %s\n' "$*" >&2; }
die() { printf '[yonder-install] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run as root (sudo bash $0 ...)"
  fi
}

detect_pack_root() {
  if [[ -n "${PACK_ROOT:-}" && -f "${PACK_ROOT}/VERSION" && -d "${PACK_ROOT}/payload" ]]; then
    printf '%s\n' "${PACK_ROOT}"
    return
  fi
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
  if [[ -f "${here}/VERSION" && -d "${here}/payload" ]]; then
    printf '%s\n' "${here}"
    return
  fi
  die "Could not find pack root (VERSION + payload/). Unpack the USB tarball and run from that directory."
}

assert_rhel_like() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in
      rhel:8*|centos:8*|rocky:8*|almalinux:8*|ol:8*)
        log "Detected ${PRETTY_NAME:-$ID $VERSION_ID}"
        return
        ;;
      rhel:9*|rocky:9*|almalinux:9*)
        warn "Pack targets RHEL 8.10; continuing on ${PRETTY_NAME} (x86_64 binaries)."
        return
        ;;
    esac
    warn "Unexpected OS (${PRETTY_NAME:-unknown}); continuing anyway."
  fi
}

assert_x86_64() {
  local arch
  arch="$(uname -m)"
  [[ "${arch}" == "x86_64" ]] || die "This pack is x86_64 only (found ${arch})"
}

ensure_yonder_user() {
  if ! getent group "${YONDER_GROUP}" >/dev/null; then
    groupadd --system "${YONDER_GROUP}"
  fi
  if ! id -u "${YONDER_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${YONDER_GROUP}" --home-dir "${YONDER_HOME}" \
      --shell /sbin/nologin "${YONDER_USER}"
  fi
}

install_tree() {
  local pack_root="$1"
  mkdir -p "${YONDER_HOME}" "${YONDER_ETC}" "${YONDER_DATA}" "${YONDER_LOG}"
  rsync -a --delete \
    --exclude 'console/node_modules' \
    "${pack_root}/payload/repo/" "${YONDER_HOME}/repo/"
  rsync -a "${pack_root}/payload/python/" "${YONDER_HOME}/python/"
  rsync -a "${pack_root}/payload/uv/" "${YONDER_HOME}/uv/"
  rsync -a "${pack_root}/payload/wheelhouse/" "${YONDER_HOME}/wheelhouse/"
  rsync -a "${pack_root}/payload/requirements/" "${YONDER_HOME}/requirements/"
  rsync -a "${pack_root}/payload/systemd/" "${YONDER_HOME}/systemd/"
  rsync -a "${pack_root}/payload/scripts/" "${YONDER_HOME}/scripts/"
  chmod +x "${YONDER_HOME}/uv/uv" || true
  find "${YONDER_HOME}/python" -type f -name 'python3*' -exec chmod +x {} \; 2>/dev/null || true
}

find_pack_python() {
  local candidate
  for candidate in \
    "${YONDER_HOME}/python/python/bin/python3" \
    "${YONDER_HOME}/python/bin/python3"
  do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  die "Bundled Python not found under ${YONDER_HOME}/python"
}

create_venv_and_install() {
  local role="$1" # gateway | worker
  local py
  py="$(find_pack_python)"
  local uv="${YONDER_HOME}/uv/uv"
  [[ -x "${uv}" ]] || die "Bundled uv missing at ${uv}"

  log "Creating venv with bundled Python (${py})"
  "${py}" -m venv "${YONDER_HOME}/venv"

  local packages
  if [[ "${role}" == "gateway" ]]; then
    packages=(yonder-contracts yonder-configuration yonder-telemetry yonder-gateway)
  else
    packages=(yonder-contracts yonder-configuration yonder-telemetry yonder-worker)
  fi

  log "Installing ${packages[*]} from offline wheelhouse"
  "${uv}" pip install \
    --python "${YONDER_HOME}/venv/bin/python" \
    --no-index \
    --find-links "${YONDER_HOME}/wheelhouse" \
    "${packages[@]}"
}

install_node_runtime() {
  local pack_root="$1"
  rsync -a "${pack_root}/payload/node/runtime/" "${YONDER_HOME}/node/"
  rsync -a "${pack_root}/payload/console/" "${YONDER_HOME}/console/"
  # node_modules may be huge; copy if present
  if [[ -d "${pack_root}/payload/console/node_modules" ]]; then
    rsync -a "${pack_root}/payload/console/node_modules/" "${YONDER_HOME}/console/node_modules/"
  fi
  if [[ -d "${pack_root}/payload/console/.next" ]]; then
    rsync -a "${pack_root}/payload/console/.next/" "${YONDER_HOME}/console/.next/"
  fi
  chmod +x "${YONDER_HOME}/node/bin/node" "${YONDER_HOME}/node/bin/npm" || true
}

rebuild_console() {
  local gateway_url="$1"
  log "Building console with NEXT_PUBLIC_YONDER_GATEWAY_URL=${gateway_url}"
  (
    cd "${YONDER_HOME}/console"
    export PATH="${YONDER_HOME}/node/bin:${PATH}"
    export NEXT_PUBLIC_YONDER_GATEWAY_URL="${gateway_url}"
    export NODE_ENV=production
    npm run build
  )
}

write_env_file() {
  local dest="$1"
  shift
  umask 077
  mkdir -p "$(dirname "${dest}")"
  : > "${dest}"
  local kv
  for kv in "$@"; do
    printf '%s\n' "${kv}" >> "${dest}"
  done
  chown root:"${YONDER_GROUP}" "${dest}"
  chmod 640 "${dest}"
}

install_unit() {
  local name="$1"
  cp "${YONDER_HOME}/systemd/${name}" "/etc/systemd/system/${name}"
  systemctl daemon-reload
}

own_runtime_dirs() {
  chown -R "${YONDER_USER}:${YONDER_GROUP}" "${YONDER_HOME}" "${YONDER_DATA}" "${YONDER_LOG}"
  # Keep env readable by service user via group.
  chown -R root:"${YONDER_GROUP}" "${YONDER_ETC}"
  chmod 750 "${YONDER_ETC}"
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local reply
  read -r -p "${prompt} [${default}]: " reply || true
  if [[ -z "${reply}" ]]; then
    printf '%s\n' "${default}"
  else
    printf '%s\n' "${reply}"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local reply
  read -r -p "${prompt} [y/N]: " reply || true
  reply="$(printf '%s' "${reply:-$default}" | tr '[:upper:]' '[:lower:]')"
  [[ "${reply}" == "y" || "${reply}" == "yes" ]]
}

system_rpm_dir() {
  local pack_root="$1"
  printf '%s\n' "${pack_root}/payload/system-rpms"
}

enable_offline_system_repo() {
  local pack_root="$1"
  local dir
  dir="$(system_rpm_dir "${pack_root}")"
  [[ -d "${dir}" ]] || die "Missing system RPM repo at ${dir}"
  [[ -f "${dir}/repodata/repomd.xml" ]] || die "Missing createrepo metadata at ${dir}/repodata (rebuild the USB pack)"
  mkdir -p /etc/yum.repos.d
  cat > /etc/yum.repos.d/yonder-offline-system.repo <<EOF
[yonder-offline-system]
name=Yonder offline system RPMs (RHEL 8)
baseurl=file://${dir}
enabled=1
gpgcheck=0
module_hotfixes=1
EOF
}

read_package_list() {
  # Prints non-comment, non-empty lines from a package list file.
  local file="$1"
  [[ -f "${file}" ]] || return 0
  grep -vE '^[[:space:]]*(#|$)' "${file}" || true
}

install_system_rpms() {
  # role: base | nvidia
  local pack_root="$1"
  local role="${2:-base}"
  local dir pkgs=() line

  dir="$(system_rpm_dir "${pack_root}")"
  if [[ ! -d "${dir}" ]] || ! compgen -G "${dir}/*.rpm" >/dev/null; then
    die "No system RPMs under ${dir}. Rebuild with fetch-system-rpms.sh + build-usb-pack.sh (fresh RHEL needs them)."
  fi

  enable_offline_system_repo "${pack_root}"

  while IFS= read -r line; do
    [[ -n "${line}" ]] && pkgs+=("${line}")
  done < <(read_package_list "${pack_root}/payload/scripts/system-packages.base.txt")

  if [[ "${role}" == "nvidia" ]]; then
    while IFS= read -r line; do
      [[ -n "${line}" ]] && pkgs+=("${line}")
    done < <(read_package_list "${pack_root}/payload/scripts/system-packages.nvidia.txt")
  fi

  [[ "${#pkgs[@]}" -gt 0 ]] || die "Empty system package list"

  log "Installing offline system packages (${role}): ${pkgs[*]}"
  if command -v dnf >/dev/null; then
    dnf -y install \
      --disablerepo='*' \
      --enablerepo=yonder-offline-system \
      --setopt=yonder-offline-system.gpgcheck=0 \
      --nogpgcheck \
      "${pkgs[@]}" \
      || die "Offline system package install failed"
  elif command -v yum >/dev/null; then
    yum -y install \
      --disablerepo='*' \
      --enablerepo=yonder-offline-system \
      --nogpgcheck \
      "${pkgs[@]}" \
      || die "Offline system package install failed"
  else
    die "dnf/yum required to install bundled system RPMs"
  fi

  if [[ "${role}" == "nvidia" ]]; then
    local kr
    kr="$(uname -r)"
    if [[ ! -d "/usr/src/kernels/${kr}" ]]; then
      warn "kernel-devel for running kernel (${kr}) is not installed."
      warn "Bundled kernel-devel may target a different 8.10 build. DKMS will fail until you add matching kernel-devel from RHEL media into payload/system-rpms and re-run."
    else
      log "kernel-devel present for ${kr}"
    fi
  fi
}

list_nvidia_rpms() {
  local pack_root="$1"
  local dir="${pack_root}/payload/nvidia"
  if [[ ! -d "${dir}" ]]; then
    return 0
  fi
  find "${dir}" -maxdepth 1 -type f \( -name '*.rpm' -o -name '*.RPM' \) | sort
}

install_nvidia_driver_rpms() {
  local pack_root="$1"
  local rpms=()
  local local_repo_rpm=""
  local nvidia_repo=""

  while IFS= read -r line; do
    [[ -n "${line}" ]] && rpms+=("${line}")
  done < <(list_nvidia_rpms "${pack_root}")

  if [[ "${#rpms[@]}" -eq 0 ]]; then
    warn "No NVIDIA RPMs found under payload/nvidia/ — skipping driver install"
    return 0
  fi

  # OS toolchain + dkms first (fresh RHEL has none of this).
  install_system_rpms "${pack_root}" nvidia
  enable_offline_system_repo "${pack_root}"

  log "Installing NVIDIA local-repo package(s):"
  printf '  %s\n' "${rpms[@]}"

  # nvidia-*.rpm from NVIDIA is typically nvidia-driver-local-repo-rhel8-*, which
  # drops a file:// yum repo under /var/nvidia-driver-local-repo-*/.
  if command -v dnf >/dev/null; then
    dnf -y localinstall --nogpgcheck "${rpms[@]}" || die "Failed to install NVIDIA local-repo RPM(s)"
  elif command -v yum >/dev/null; then
    yum -y localinstall --nogpgcheck "${rpms[@]}" || die "Failed to install NVIDIA local-repo RPM(s)"
  else
    rpm -Uvh "${rpms[@]}" || die "rpm install failed for NVIDIA RPMs"
  fi

  local_repo_rpm="$(rpm -qa 'nvidia-driver-local-repo*' 2>/dev/null | head -1 || true)"
  nvidia_repo="$(find /var -maxdepth 1 -type d -name 'nvidia-driver-local-repo-*' 2>/dev/null | head -1 || true)"
  if [[ -z "${nvidia_repo}" ]]; then
    die "NVIDIA local-repo directory not found under /var after installing ${local_repo_rpm:-RPMs}"
  fi
  if [[ -f "${nvidia_repo}/3183BF0F.pub" ]]; then
    rpm --import "${nvidia_repo}/3183BF0F.pub" || warn "Could not import NVIDIA repo GPG key"
  fi

  log "Enabling nvidia-driver:580-dkms and installing cuda-drivers from local repo (offline)"
  if ! command -v dnf >/dev/null; then
    die "dnf is required to install cuda-drivers from the NVIDIA local repo"
  fi

  # Prefer only local NVIDIA + our system RPM repo (no internet).
  dnf -y module enable nvidia-driver:580-dkms \
    --disablerepo='*' \
    --enablerepo='nvidia-driver-local*' \
    --enablerepo=yonder-offline-system \
    --nogpgcheck \
    || die "Failed to enable nvidia-driver:580-dkms module"

  dnf -y install cuda-drivers \
    --disablerepo='*' \
    --enablerepo='nvidia-driver-local*' \
    --enablerepo=yonder-offline-system \
    --nogpgcheck \
    || die "Failed to install cuda-drivers offline"

  log "NVIDIA driver install finished."
  log "A reboot is usually required before nvidia-smi works (nouveau blacklist / kernel module)."
  if command -v nvidia-smi >/dev/null; then
    nvidia-smi || warn "nvidia-smi present but returned an error (reboot may still be required)"
  else
    warn "nvidia-smi not on PATH yet — reboot after install, then re-check"
  fi
}
