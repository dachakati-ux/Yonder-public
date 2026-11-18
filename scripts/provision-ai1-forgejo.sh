#!/usr/bin/env bash
# Provision loopback-only Forgejo on ai1 for lab company tooling repos.
#
# LAB ONLY — PENDING SECURITY REVIEW / REVISION
# Binds 127.0.0.1:3001 only. Coworkers reach it via SSH tunnel:
#   ssh -N -L 3001:127.0.0.1:3001 ai1
#
# Usage (on ai1, as root):
#   # Air-gapped (preferred): stage binary on a networked host, then:
#   scp forgejo-*-linux-amd64 ai1:/opt/yonder/
#   sudo FORGEJO_BINARY=/opt/yonder/forgejo-16.0.2-linux-amd64 \
#     /opt/yonder/src/scripts/provision-ai1-forgejo.sh
#
#   # With outbound HTTPS from ai1 (unusual):
#   sudo /opt/yonder/src/scripts/provision-ai1-forgejo.sh
#
# Optional first admin (created only when both are set and no users exist).
# Note: username "admin" is reserved by Forgejo — use labadmin (or similar).
#   sudo FORGEJO_ADMIN_USER=labadmin FORGEJO_ADMIN_PASSWORD='…' \
#     FORGEJO_ADMIN_EMAIL=labadmin@localhost.local \
#     …/provision-ai1-forgejo.sh
#
# Does NOT enable Yonder ForgejoAdapter / coder PR automation.
set -euo pipefail

FORGEJO_VERSION="${FORGEJO_VERSION:-16.0.2}"
FORGEJO_BINARY="${FORGEJO_BINARY:-}"
FORGEJO_DOWNLOAD_BASE="${FORGEJO_DOWNLOAD_BASE:-https://code.forgejo.org/forgejo/forgejo/releases/download}"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/yonder/forgejo}"
DATA_ROOT="${DATA_ROOT:-/srv/yonder-storage/forgejo}"
CONFIG_FILE="${CONFIG_FILE:-/etc/yonder/forgejo.ini}"
ENV_FILE="${ENV_FILE:-/etc/yonder/forgejo.env}"
UNIT_DST="${UNIT_DST:-/etc/systemd/system/yonder-forgejo.service}"
HTTP_ADDR="${HTTP_ADDR:-127.0.0.1}"
HTTP_PORT="${HTTP_PORT:-3001}"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
REPO_SRC="${REPO_SRC:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_SRC="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_SRC="${REPO_SRC:-${DEFAULT_REPO_SRC}}"

log() { printf '[provision-ai1-forgejo] %s\n' "$*"; }
die() { printf '[provision-ai1-forgejo] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"

case "${HTTP_ADDR}" in
  127.0.0.1|localhost|::1) ;;
  *)
    die "Refusing non-loopback HTTP_ADDR=${HTTP_ADDR}. Use 127.0.0.1 (plan 2A)."
    ;;
esac

if ! command -v git >/dev/null 2>&1; then
  log "git not found — installing (required by Forgejo)"
  if command -v dnf >/dev/null 2>&1; then
    dnf -y install git || die "Failed to install git via dnf"
  elif command -v yum >/dev/null 2>&1; then
    yum -y install git || die "Failed to install git via yum"
  else
    die "git is required and no dnf/yum available"
  fi
fi
command -v git >/dev/null || die "git still missing after install attempt"

id -u "${SERVICE_USER}" >/dev/null 2>&1 || die "Service user ${SERVICE_USER} missing"
[[ -d /srv/yonder-storage ]] || die "/srv/yonder-storage missing — provision storage first"
mountpoint -q /srv/yonder-storage || log "WARNING: /srv/yonder-storage is not a mountpoint"

UNIT_SRC="${REPO_SRC}/packaging/offline/systemd/yonder-forgejo.service"
INI_SRC="${REPO_SRC}/config/lab/forgejo.ini.example"
[[ -f "${UNIT_SRC}" ]] || die "Missing unit template: ${UNIT_SRC}"
[[ -f "${INI_SRC}" ]] || die "Missing ini template: ${INI_SRC}"

mkdir -p /etc/yonder "${INSTALL_ROOT}" \
  "${DATA_ROOT}/data" \
  "${DATA_ROOT}/repositories" \
  "${DATA_ROOT}/lfs" \
  "${DATA_ROOT}/log" \
  "${DATA_ROOT}/custom"

install_binary() {
  local dest="${INSTALL_ROOT}/forgejo"
  if [[ -n "${FORGEJO_BINARY}" ]]; then
    [[ -f "${FORGEJO_BINARY}" ]] || die "FORGEJO_BINARY not found: ${FORGEJO_BINARY}"
    log "Installing binary from ${FORGEJO_BINARY}"
    install -m 0755 "${FORGEJO_BINARY}" "${dest}"
    return
  fi
  local name="forgejo-${FORGEJO_VERSION}-linux-amd64"
  local url="${FORGEJO_DOWNLOAD_BASE}/v${FORGEJO_VERSION}/${name}"
  local stage="/tmp/${name}"
  if [[ -x "${dest}" ]]; then
    log "Binary already present at ${dest}; skipping download"
    return
  fi
  log "Downloading ${url}"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "${stage}" "${url}" || die "Download failed (air-gap? set FORGEJO_BINARY=…)"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "${stage}" "${url}" || die "Download failed (air-gap? set FORGEJO_BINARY=…)"
  else
    die "curl/wget missing; set FORGEJO_BINARY to a staged forgejo linux-amd64 binary"
  fi
  install -m 0755 "${stage}" "${dest}"
  rm -f "${stage}"
}

write_config() {
  log "Writing ${CONFIG_FILE}"
  local internal_token
  internal_token="$(openssl rand -hex 32)"
  install -m 0640 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" /dev/null "${CONFIG_FILE}"
  # shellcheck disable=SC2002
  cat "${INI_SRC}" >"${CONFIG_FILE}"
  # Ensure loopback values win even if template drifts.
  if grep -q '^HTTP_ADDR' "${CONFIG_FILE}"; then
    sed -i "s/^HTTP_ADDR = .*/HTTP_ADDR = ${HTTP_ADDR}/" "${CONFIG_FILE}"
  else
    die "HTTP_ADDR missing from template"
  fi
  sed -i "s/^HTTP_PORT = .*/HTTP_PORT = ${HTTP_PORT}/" "${CONFIG_FILE}"
  sed -i "s|^ROOT_URL = .*|ROOT_URL = http://${HTTP_ADDR}:${HTTP_PORT}/|" "${CONFIG_FILE}"
  # Pre-seed INTERNAL_TOKEN so migrate (as yonder) does not need to rewrite
  # a root-owned file under /etc/yonder.
  if grep -q '^INTERNAL_TOKEN' "${CONFIG_FILE}"; then
    sed -i "s|^INTERNAL_TOKEN = .*|INTERNAL_TOKEN = ${internal_token}|" "${CONFIG_FILE}"
  else
    # Insert under [security] after INSTALL_LOCK.
    sed -i "/^INSTALL_LOCK = /a INTERNAL_TOKEN = ${internal_token}" "${CONFIG_FILE}"
  fi
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${CONFIG_FILE}"
  chmod 0640 "${CONFIG_FILE}"
  grep -E '^HTTP_ADDR = 127\.0\.0\.1$' "${CONFIG_FILE}" >/dev/null \
    || die "Config HTTP_ADDR is not 127.0.0.1 after write — refuse to start"
}

ensure_secret() {
  if [[ -f "${ENV_FILE}" ]] && grep -q '^FORGEJO__SECURITY__SECRET_KEY=' "${ENV_FILE}"; then
    log "Reusing existing ${ENV_FILE}"
    chmod 0600 "${ENV_FILE}"
    chown root:"${SERVICE_GROUP}" "${ENV_FILE}"
    return
  fi
  log "Generating ${ENV_FILE}"
  local key
  key="$(openssl rand -hex 32)"
  umask 077
  cat >"${ENV_FILE}" <<EOF
# LAB ONLY — PENDING SECURITY REVIEW / REVISION
# Managed by scripts/provision-ai1-forgejo.sh — do not commit.
FORGEJO__SECURITY__SECRET_KEY=${key}
EOF
  chmod 0600 "${ENV_FILE}"
  chown root:"${SERVICE_GROUP}" "${ENV_FILE}"
}

run_as_yonder() {
  # shellcheck disable=SC2086
  runuser -u "${SERVICE_USER}" -- env \
    FORGEJO__SECURITY__SECRET_KEY="$(grep '^FORGEJO__SECURITY__SECRET_KEY=' "${ENV_FILE}" | cut -d= -f2-)" \
    "$@"
}

migrate_db() {
  log "Running forgejo migrate"
  run_as_yonder "${INSTALL_ROOT}/forgejo" migrate --config "${CONFIG_FILE}"
}

maybe_create_admin() {
  if [[ -z "${FORGEJO_ADMIN_USER:-}" || -z "${FORGEJO_ADMIN_PASSWORD:-}" ]]; then
    log "Skipping admin create (set FORGEJO_ADMIN_USER + FORGEJO_ADMIN_PASSWORD to create)"
    return
  fi
  if [[ "${FORGEJO_ADMIN_USER}" == "admin" ]]; then
    die "Forgejo reserves the username 'admin'. Use FORGEJO_ADMIN_USER=labadmin (or similar)."
  fi
  local email="${FORGEJO_ADMIN_EMAIL:-${FORGEJO_ADMIN_USER}@localhost.local}"
  log "Ensuring admin user ${FORGEJO_ADMIN_USER}"
  if ! run_as_yonder "${INSTALL_ROOT}/forgejo" admin user create \
    --config "${CONFIG_FILE}" \
    --admin \
    --username "${FORGEJO_ADMIN_USER}" \
    --password "${FORGEJO_ADMIN_PASSWORD}" \
    --email "${email}" \
    --must-change-password=false; then
    log "Admin create returned non-zero (user may already exist) — listing users"
    run_as_yonder "${INSTALL_ROOT}/forgejo" admin user list --config "${CONFIG_FILE}" || true
  fi
}

install_unit() {
  log "Installing ${UNIT_DST}"
  install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
  systemctl daemon-reload
  systemctl enable yonder-forgejo.service
  systemctl restart yonder-forgejo.service
}

verify_loopback() {
  local i line
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -o /dev/null "http://${HTTP_ADDR}:${HTTP_PORT}/"; then
      break
    fi
    sleep 1
  done
  curl -fsS -o /dev/null "http://${HTTP_ADDR}:${HTTP_PORT}/" \
    || die "Forgejo did not become ready on http://${HTTP_ADDR}:${HTTP_PORT}/"

  if command -v ss >/dev/null 2>&1; then
    while read -r line; do
      case "${line}" in
        *127.0.0.1:${HTTP_PORT}*|*\[::1\]:${HTTP_PORT}*) ;;
        *:*)
          systemctl stop yonder-forgejo.service || true
          die "Forgejo listening on non-loopback address: ${line}"
          ;;
      esac
    done < <(ss -ltn | awk -v p=":${HTTP_PORT}" 'index($4, p) && substr($4, length($4)-length(p)+1) == p {print $4}')
    log "Listen check:"
    ss -ltn | grep "${HTTP_PORT}" || true
  fi
  systemctl is-active --quiet yonder-forgejo.service || die "yonder-forgejo is not active"
  log "OK — Forgejo on http://${HTTP_ADDR}:${HTTP_PORT}/ (loopback). Tunnel from laptop:"
  log "  ssh -N -L ${HTTP_PORT}:${HTTP_ADDR}:${HTTP_PORT} ai1"
  log "Coder ForgejoAdapter remains fail-closed until a later Phase L slice."
}

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${DATA_ROOT}"
install_binary
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}"
write_config
ensure_secret
migrate_db
maybe_create_admin
install_unit
verify_loopback
