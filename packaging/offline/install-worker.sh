#!/usr/bin/env bash
# Offline worker installer for RHEL 8.10.
# Run from the unpacked USB pack on Server B/C (no internet required).
#
#   sudo bash install-worker.sh
#   sudo bash install-worker.sh \
#     --gateway-url http://control-plane.example.internal:8080 \
#     --worker-id worker-ai3 \
#     --public-endpoint http://worker-01.example.internal:8090 \
#     --install-nvidia \
#     --non-interactive
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${PACK_ROOT}/payload/scripts/lib.sh"

GATEWAY_URL=""
WORKER_ID=""
PUBLIC_ENDPOINT=""
DISPLAY_NAME=""
NONINTERACTIVE=0
BIND_HOST="0.0.0.0"
INSTALL_NVIDIA="" # empty=ask (interactive), 0=no, 1=yes

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gateway-url) GATEWAY_URL="$2"; shift 2 ;;
    --worker-id) WORKER_ID="$2"; shift 2 ;;
    --public-endpoint) PUBLIC_ENDPOINT="$2"; shift 2 ;;
    --display-name) DISPLAY_NAME="$2"; shift 2 ;;
    --bind-host) BIND_HOST="$2"; shift 2 ;;
    --install-nvidia) INSTALL_NVIDIA=1; shift ;;
    --skip-nvidia) INSTALL_NVIDIA=0; shift ;;
    --non-interactive) NONINTERACTIVE=1; shift ;;
    -h|--help)
      sed -n '1,16p' "$0"
      exit 0
      ;;
    *) die "Unknown arg: $1" ;;
  esac
done

require_root
assert_x86_64
assert_rhel_like
command -v systemctl >/dev/null || die "systemd is required"

PACK_ROOT="$(detect_pack_root)"
log "Using pack at ${PACK_ROOT}"

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-127.0.0.1}"

if [[ "${NONINTERACTIVE}" -eq 0 ]]; then
  GATEWAY_URL="${GATEWAY_URL:-$(prompt_default "Control-plane Gateway URL" "http://control-plane.example.internal:8080")}"
  WORKER_ID="${WORKER_ID:-$(prompt_default "Worker id (must be allowlisted)" "worker-ai3")}"
  PUBLIC_ENDPOINT="${PUBLIC_ENDPOINT:-$(prompt_default "Public endpoint (Gateway → this host)" "http://${HOST_IP}:8090")}"
  DISPLAY_NAME="${DISPLAY_NAME:-$(prompt_default "Display name" "${WORKER_ID}")}"
  if [[ -z "${INSTALL_NVIDIA}" ]]; then
    if list_nvidia_rpms "${PACK_ROOT}" | grep -q .; then
      if prompt_yes_no "Install bundled NVIDIA driver RPM(s) on this host?"; then
        INSTALL_NVIDIA=1
      else
        INSTALL_NVIDIA=0
      fi
    else
      INSTALL_NVIDIA=0
    fi
  fi
else
  [[ -n "${GATEWAY_URL}" ]] || die "--gateway-url required"
  [[ -n "${WORKER_ID}" ]] || die "--worker-id required"
  [[ -n "${PUBLIC_ENDPOINT}" ]] || die "--public-endpoint required"
  DISPLAY_NAME="${DISPLAY_NAME:-${WORKER_ID}}"
  INSTALL_NVIDIA="${INSTALL_NVIDIA:-0}"
fi

case "${PUBLIC_ENDPOINT}" in
  *0.0.0.0*) die "Public endpoint must be a Gateway-reachable host IP/DNS, not 0.0.0.0" ;;
esac

# Fresh RHEL minimal: install OS packages before rsync / app payload.
# NVIDIA path also pulls DKMS toolchain and installs cuda-drivers from the local-repo RPM.
if [[ "${INSTALL_NVIDIA}" == "1" ]]; then
  install_nvidia_driver_rpms "${PACK_ROOT}"
else
  install_system_rpms "${PACK_ROOT}" base
fi
command -v rsync >/dev/null || die "rsync missing after system RPM install"

ensure_yonder_user
log "Installing worker payload into ${YONDER_HOME}"
install_tree "${PACK_ROOT}"
create_venv_and_install worker

write_env_file "${YONDER_ETC}/worker.env" \
  "YONDER_ENVIRONMENT=lab" \
  "YONDER_CONFIG_FILE=${YONDER_HOME}/repo/config/environments/lab-multinode.yaml" \
  "YONDER_SECURITY_MODE=development" \
  "YONDER_ALLOW_NON_LOCALHOST_BIND_IN_DEVELOPMENT=true" \
  "YONDER_WORKER__HOST=${BIND_HOST}" \
  "YONDER_WORKER__PORT=8090" \
  "YONDER_GATEWAY_URL=${GATEWAY_URL}" \
  "YONDER_WORKER_ID=${WORKER_ID}" \
  "YONDER_WORKER_DISPLAY_NAME=${DISPLAY_NAME}" \
  "YONDER_WORKER_PUBLIC_ENDPOINT=${PUBLIC_ENDPOINT}"

install_unit yonder-worker.service
own_runtime_dirs

systemctl enable --now yonder-worker.service

if [[ -x "${PACK_ROOT}/configure-selinux.sh" ]]; then
  bash "${PACK_ROOT}/configure-selinux.sh" || warn "SELinux helper reported issues (continuing)"
fi
if [[ -x "${PACK_ROOT}/configure-firewall.sh" ]]; then
  bash "${PACK_ROOT}/configure-firewall.sh" --role worker || warn "Firewall helper reported issues (continuing)"
fi
if [[ -x "${PACK_ROOT}/verify-install.sh" ]]; then
  bash "${PACK_ROOT}/verify-install.sh" --role worker --gateway-url "${GATEWAY_URL}" \
    || warn "Post-install verification reported issues"
fi

log "Worker installed."
log "  id:       ${WORKER_ID}"
log "  gateway:  ${GATEWAY_URL}"
log "  public:   ${PUBLIC_ENDPOINT}"
log "  Status:   systemctl status yonder-worker"
log "  Logs:     journalctl -u yonder-worker -f"
if [[ "${INSTALL_NVIDIA}" == "1" ]]; then
  log "NVIDIA RPMs were installed — reboot this host before relying on nvidia-smi / GPU inventory."
fi
log "  Verify:   bash verify-install.sh --role worker --gateway-url ${GATEWAY_URL}"
log "Confirm the worker on the control-plane Hardware / Operations pages."
