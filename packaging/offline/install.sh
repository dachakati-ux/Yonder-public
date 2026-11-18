#!/usr/bin/env bash
# Offline control-plane installer for RHEL 8.10 (Gateway + Console).
# Run from the unpacked offline pack on Server A (no internet required).
#
#   sudo bash install.sh
#   sudo bash install.sh --gateway-url http://control-plane.example.internal:8080 --non-interactive
set -euo pipefail

PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${PACK_ROOT}/payload/scripts/lib.sh"

GATEWAY_URL=""
CONSOLE_ORIGIN=""
MODEL_ROOT="/srv/yonder/models"
TAKE_MODEL_OWNERSHIP=0
NONINTERACTIVE=0
BIND_HOST="0.0.0.0"
ALLOWLIST="worker-ai3,worker-ai7"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gateway-url) GATEWAY_URL="$2"; shift 2 ;;
    --console-origin) CONSOLE_ORIGIN="$2"; shift 2 ;;
    --model-root) MODEL_ROOT="$2"; shift 2 ;;
    --take-model-ownership) TAKE_MODEL_OWNERSHIP=1; shift ;;
    --allowlist) ALLOWLIST="$2"; shift 2 ;;
    --bind-host) BIND_HOST="$2"; shift 2 ;;
    --non-interactive) NONINTERACTIVE=1; shift ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *) die "Unknown arg: $1" ;;
  esac
done

require_root
assert_x86_64
assert_rhel_like
command -v systemctl >/dev/null || die "systemd is required"

[[ "${MODEL_ROOT}" == /* ]] || die "--model-root must be an absolute path"
[[ "${MODEL_ROOT}" =~ ^/[A-Za-z0-9._/-]+$ ]] \
  || die "--model-root may contain only letters, numbers, dot, underscore, dash, and slash"

PACK_ROOT="$(detect_pack_root)"
log "Using pack at ${PACK_ROOT}"
[[ -f "${PACK_ROOT}/VERSION" ]] && log "Pack $(tr '\n' ' ' < "${PACK_ROOT}/VERSION")"

# Fresh RHEL minimal has no rsync — bootstrap OS packages from the offline pack first.
install_system_rpms "${PACK_ROOT}" base
command -v rsync >/dev/null || die "rsync missing after system RPM install"

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-127.0.0.1}"

if [[ "${NONINTERACTIVE}" -eq 0 ]]; then
  GATEWAY_URL="${GATEWAY_URL:-$(prompt_default "Public Gateway URL (browsers + workers)" "http://${HOST_IP}:8080")}"
  CONSOLE_ORIGIN="${CONSOLE_ORIGIN:-$(prompt_default "Console origin (CORS)" "http://${HOST_IP}:3000")}"
  ALLOWLIST="${ALLOWLIST:-$(prompt_default "Worker allowlist (comma-separated)" "${ALLOWLIST}")}"
else
  [[ -n "${GATEWAY_URL}" ]] || die "--gateway-url required with --non-interactive"
  CONSOLE_ORIGIN="${CONSOLE_ORIGIN:-http://${HOST_IP}:3000}"
fi

ensure_yonder_user
if [[ ! -e "${MODEL_ROOT}" ]]; then
  mkdir -p "${MODEL_ROOT}"
  chown "${YONDER_USER}:${YONDER_GROUP}" "${MODEL_ROOT}"
fi
[[ -d "${MODEL_ROOT}" ]] || die "Model root is not a directory: ${MODEL_ROOT}"
MODEL_ROOT="$(readlink -f -- "${MODEL_ROOT}")"
MODEL_FSTYPE="$(findmnt -n -o FSTYPE -T "${MODEL_ROOT}" 2>/dev/null || true)"
case "${MODEL_FSTYPE}" in
  exfat|fuse.exfat|fuseblk)
    die "Model root uses unsupported ${MODEL_FSTYPE}. Use an XFS or ext4 server volume; RHEL 8 does not support exFAT."
    ;;
esac
if [[ "${TAKE_MODEL_OWNERSHIP}" -eq 1 ]]; then
  log "Assigning ${MODEL_ROOT} ownership to ${YONDER_USER}:${YONDER_GROUP}"
  chown -R "${YONDER_USER}:${YONDER_GROUP}" "${MODEL_ROOT}"
fi
if ! runuser -u "${YONDER_USER}" -- test -r "${MODEL_ROOT}"; then
  die "Model root is not readable by ${YONDER_USER}: ${MODEL_ROOT}. Fix mount uid/gid/mode options and retry."
fi
if ! runuser -u "${YONDER_USER}" -- test -w "${MODEL_ROOT}"; then
  die "Model root is not writable by ${YONDER_USER}: ${MODEL_ROOT}. Folder preparation writes bundle.yaml files."
fi
while IFS= read -r -d '' model_dir; do
  if ! runuser -u "${YONDER_USER}" -- test -r "${model_dir}" \
    || ! runuser -u "${YONDER_USER}" -- test -w "${model_dir}"; then
    die "Model folder is not read/write accessible by ${YONDER_USER}: ${model_dir}. Use --take-model-ownership or fix permissions."
  fi
done < <(find "${MODEL_ROOT}" -mindepth 1 -maxdepth 1 -type d -print0)
log "Installing payload into ${YONDER_HOME}"
install_tree "${PACK_ROOT}"
install_node_runtime "${PACK_ROOT}"
if grep -q '^status=staged$' "${PACK_ROOT}/payload/model-bundles/STATUS.txt" 2>/dev/null; then
  log "Importing staged model bundles into ${MODEL_ROOT}"
  rsync -a --ignore-existing \
    --exclude STATUS.txt \
    "${PACK_ROOT}/payload/model-bundles/" "${MODEL_ROOT}/"
fi

create_venv_and_install gateway

write_env_file "${YONDER_ETC}/control-plane.env" \
  "YONDER_ENVIRONMENT=lab" \
  "YONDER_REPO_ROOT=${YONDER_HOME}/repo" \
  "YONDER_CONFIG_FILE=${YONDER_HOME}/repo/config/environments/lab-multinode.yaml" \
  "YONDER_SECURITY_MODE=development" \
  "YONDER_ALLOW_NON_LOCALHOST_BIND_IN_DEVELOPMENT=true" \
  "YONDER_GATEWAY__HOST=${BIND_HOST}" \
  "YONDER_GATEWAY__PORT=8080" \
  "YONDER_CONSOLE_ORIGIN=${CONSOLE_ORIGIN}" \
  "YONDER_CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000,${CONSOLE_ORIGIN}" \
  "YONDER_DATABASE_URL=sqlite:///${YONDER_DATA}/yonder.db" \
  "YONDER_STATE_FILE=${YONDER_DATA}/gateway-state.json" \
  "YONDER_MODEL_ROOTS=${MODEL_ROOT}" \
  "YONDER_WORKER_REGISTRATION__MODE=allowlist" \
  "YONDER_WORKER_REGISTRATION__ALLOWED_WORKER_IDS=${ALLOWLIST}" \
  "YONDER_WORKER_REGISTRATION__ACCEPT_UNTRUSTED=false" \
  "NEXT_PUBLIC_YONDER_GATEWAY_URL=${GATEWAY_URL}"

mkdir -p "${YONDER_DATA}"

rebuild_console "${GATEWAY_URL}"

log "Running database migrations"
mkdir -p "${YONDER_DATA}"
(
  cd "${YONDER_HOME}/repo"
  export YONDER_DATABASE_URL="sqlite:///${YONDER_DATA}/yonder.db"
  export YONDER_CONFIG_FILE="${YONDER_HOME}/repo/config/environments/lab-multinode.yaml"
  # shellcheck disable=SC1091
  set -a
  # shellcheck disable=SC1090
  source "${YONDER_ETC}/control-plane.env"
  set +a
  "${YONDER_HOME}/venv/bin/alembic" -c database/alembic.ini upgrade head
)

install_unit yonder-gateway.service
install_unit yonder-console.service
mkdir -p /etc/systemd/system/yonder-gateway.service.d
cat > /etc/systemd/system/yonder-gateway.service.d/model-root.conf <<EOF
[Service]
ReadWritePaths=${MODEL_ROOT}
EOF
systemctl daemon-reload
own_runtime_dirs

systemctl enable --now yonder-gateway.service
systemctl enable --now yonder-console.service

if [[ -x "${PACK_ROOT}/configure-selinux.sh" ]]; then
  bash "${PACK_ROOT}/configure-selinux.sh" --model-root "${MODEL_ROOT}" || warn "SELinux helper reported issues (continuing)"
fi
if [[ -x "${PACK_ROOT}/configure-firewall.sh" ]]; then
  bash "${PACK_ROOT}/configure-firewall.sh" --role control-plane || warn "Firewall helper reported issues (continuing)"
fi
if [[ -x "${PACK_ROOT}/verify-install.sh" ]]; then
  bash "${PACK_ROOT}/verify-install.sh" --role control-plane --gateway-url "${GATEWAY_URL}" --console-url "${CONSOLE_ORIGIN}" \
    --model-root "${MODEL_ROOT}" \
    || warn "Post-install verification reported issues"
fi

log "Control plane installed."
log "  Gateway: ${GATEWAY_URL}"
log "  Console: ${CONSOLE_ORIGIN}"
log "  Models:  ${MODEL_ROOT}"
log "  Status:  systemctl status yonder-gateway yonder-console"
log "  Logs:    journalctl -u yonder-gateway -u yonder-console -f"
log "  Verify:  bash verify-install.sh --role control-plane"
log "Next: on worker hosts run install-worker.sh with this Gateway URL."
