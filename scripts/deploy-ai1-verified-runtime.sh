#!/usr/bin/env bash
# Operator-assisted deploy of verified all-model runtime code onto ai1.
# Requires sudo on the host. Does NOT invent aliases; only installs code/manifests
# and restarts services so Gateway canaries can run.
#
# IMPORTANT: ai1 runs the gateway/worker from wheels installed in /opt/yonder/venv,
# not from the source tree. Syncing source alone leaves the OLD code running while
# the NEW config/models manifests are read from disk, which crash-loops the gateway
# on manifest fields the old schema rejects. This script installs wheels too.
#
# ai1 is air-gapped: every install step is --offline/--no-index against a local
# wheelhouse. Build the yonder wheels on a networked machine first:
#   python -m build --wheel --no-isolation --outdir /tmp/yonder-wheels packages/contracts
#   (repeat for packages/configuration packages/telemetry apps/gateway apps/worker)
#   scp /tmp/yonder-wheels/*.whl ai1:/opt/yonder/yonder-reinstall-wheels/
set -euo pipefail

SRC="${1:-}"
DEST="${DEST:-/opt/yonder/repo}"
WHEELS="${WHEELS:-/opt/yonder/yonder-reinstall-wheels}"
WHEELHOUSE="${WHEELHOUSE:-/opt/yonder/wheelhouse}"
VENV_PY="${VENV_PY:-/opt/yonder/venv/bin/python}"
WORKER_VENV_PY="${WORKER_VENV_PY:-/opt/yonder/venv-worker/bin/python}"
UV="${UV:-/opt/yonder/uv/uv}"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
ENV_FILE="${ENV_FILE:-/etc/yonder/control-plane.env}"
CONSOLE_DEST="${CONSOLE_DEST:-/opt/yonder/console}"
CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:3000/}"
CONSOLE_GATEWAY_URL="${CONSOLE_GATEWAY_URL:-http://127.0.0.1:8080}"
# Each repo backup is a full copy (~3 GiB with vendored artifacts), so keep a
# short history rather than filling the filesystem across repeated deploys.
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"

die() { printf '[deploy-ai1] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[deploy-ai1] %s\n' "$*"; }

[[ -n "${SRC}" ]] || die "Usage: sudo $0 /path/to/synced/yonder-checkout"
[[ -d "${SRC}/apps/gateway" ]] || die "SRC does not look like a Yonder checkout: ${SRC}"
[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
[[ -x "${UV}" ]] || die "Bundled uv missing at ${UV}"
[[ -x "${VENV_PY}" ]] || die "Service venv python missing at ${VENV_PY}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${DEST}.bak-${STAMP}"
log "backing up ${DEST} → ${BACKUP}"
cp -a "${DEST}" "${BACKUP}"

log "syncing code (preserving local data/ and .env*)"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude '.next' \
  --exclude 'data/' \
  --exclude 'dist/' \
  --exclude '.env*' \
  --exclude '__pycache__' \
  "${SRC}/" "${DEST}/"

# The venvs hold the code that actually runs. Gateway and worker have SEPARATE
# venvs; installing only into /opt/yonder/venv leaves the worker on stale code.
install_wheels() {
  local venv_py="$1" role="$2"
  local venv_root="${venv_py%/bin/python}"
  [[ -x "${venv_py}" ]] || die "venv python missing at ${venv_py}"
  log "installing ${role} wheels into ${venv_root} (offline)"
  # --no-deps plus per-package --reinstall-package keeps third-party dependencies
  # exactly as installed. A blanket --reinstall re-resolves alembic/sqlalchemy/
  # psycopg from the wheelhouse and churns the working set.
  "${UV}" pip install \
    --python "${venv_py}" \
    --offline \
    --no-index \
    --no-deps \
    --find-links "${WHEELS}" \
    --find-links "${WHEELHOUSE}" \
    --reinstall-package yonder-contracts \
    --reinstall-package yonder-configuration \
    --reinstall-package yonder-telemetry \
    --reinstall-package "yonder-${role}" \
    "${WHEELS}"/yonder_contracts-*.whl \
    "${WHEELS}"/yonder_configuration-*.whl \
    "${WHEELS}"/yonder_telemetry-*.whl \
    "${WHEELS}/yonder_${role}"-*.whl

  # uv runs as root here, so new files land root-owned under a root umask. The
  # services run as yonder against a 0750 venv, so anything root-owned becomes
  # unreadable and ExecStartPre dies with EACCES. Normalize after every install.
  log "restoring ${SERVICE_USER}:${SERVICE_GROUP} ownership on ${venv_root}"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${venv_root}"
  chmod -R u+rwX "${venv_root}"
}

if [[ -d "${WHEELS}" ]] && compgen -G "${WHEELS}/yonder_*.whl" >/dev/null; then
  install_wheels "${VENV_PY}" gateway
  if [[ -x "${WORKER_VENV_PY}" ]]; then
    install_wheels "${WORKER_VENV_PY}" worker
  else
    log "WARNING: no worker venv at ${WORKER_VENV_PY}; worker code not updated"
  fi
else
  die "No yonder wheels found in ${WHEELS}; source-only deploy would leave stale code running"
fi

# Rehearse ExecStartPre before restarting anything. This must reproduce the unit
# environment: running alembic without the EnvironmentFile leaves
# YONDER_DATABASE_URL unset, so alembic.ini's sqlite default is used and the check
# fails on a database the service never touches.
if [[ -x "$(command -v systemd-run || true)" ]] && [[ -r "${ENV_FILE}" ]]; then
  log "rehearsing ExecStartPre migrations as ${SERVICE_USER} with ${ENV_FILE}"
  if ! systemd-run \
      --uid="${SERVICE_USER}" --gid="${SERVICE_GROUP}" \
      -p WorkingDirectory="${DEST}" \
      -p EnvironmentFile="${ENV_FILE}" \
      --pipe --wait --quiet \
      "${VENV_PY}" -m alembic -c database/alembic.ini upgrade head; then
    die "alembic upgrade failed after wheel install; services untouched. Rollback: rm -rf ${DEST}; mv ${BACKUP} ${DEST}"
  fi
else
  log "skipping migration rehearsal (systemd-run or ${ENV_FILE} unavailable);"
  log "  ExecStartPre will run it and failures are captured after restart"
fi

log "verifying installed schema understands the new manifest fields"
"${VENV_PY}" - <<'PYCHECK'
import sys

from app.models.manifest import ModelRuntime
from yonder_configuration.models import YonderSettings

fields = getattr(ModelRuntime, "model_fields", {})
missing = [f for f in ("profile", "pooling", "embedding", "reranking") if f not in fields]
if missing:
    sys.exit(f"installed gateway wheel is stale; ModelRuntime missing {missing}")
settings_fields = getattr(YonderSettings, "model_fields", {})
if "diffusers" not in settings_fields:
    sys.exit("installed yonder-configuration is stale; settings.diffusers missing")
print("installed schema OK")
PYCHECK

log "restoring any quarantined manifests"
shopt -s nullglob
for quarantine in "${DEST}"/config/models/.quarantine-*/; do
  for manifest in "${quarantine}"*.yaml; do
    mv "${manifest}" "${DEST}/config/models/$(basename "${manifest}")"
    log "restored $(basename "${manifest}")"
  done
  rmdir "${quarantine}" 2>/dev/null || true
done
shopt -u nullglob

log "ensuring generated-output root exists"
mkdir -p /srv/yonder-storage/generated
chown -R yonder:yonder /srv/yonder-storage/generated || true
chmod 0755 /srv/yonder-storage/generated
find /srv/yonder-storage/model-bundles/derived -type d -exec chmod 0755 {} \; 2>/dev/null || true
find /srv/yonder-storage/model-bundles/derived -type f -exec chmod 0644 {} \; 2>/dev/null || true

log "restarting gateway + worker (llama sidecar left alone)"
# Do not let a failed restart abort before diagnostics are captured.
if ! systemctl restart yonder-gateway yonder-worker; then
  log "systemctl restart reported failure; capturing diagnostics"
  systemctl status yonder-gateway --no-pager -l | tail -25 || true
  journalctl -u yonder-gateway -n 80 --no-pager | tail -80 || true
  die "restart failed. Rollback: systemctl stop yonder-gateway yonder-worker; rm -rf ${DEST}; mv ${BACKUP} ${DEST}; systemctl start yonder-gateway yonder-worker"
fi
for attempt in $(seq 1 20); do
  sleep 2
  if curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    log "gateway healthy after ${attempt} check(s)"
    break
  fi
  if [[ "${attempt}" -eq 20 ]]; then
    systemctl status yonder-gateway --no-pager -l | tail -20 || true
    journalctl -u yonder-gateway -n 60 --no-pager | tail -60 || true
    die "gateway did not become healthy; see rollback below"
  fi
done
# The console is a separate Next.js install served by yonder-console; it runs a
# prebuilt .next because ai1 has no npm registry. Nothing above touches it, so a
# UI change would otherwise stay invisible after a successful deploy.
json_field() {
  sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -1
}

refresh_console() {
  local src="${SRC}/apps/console"
  local meta="${src}/.next/yonder-build-meta.json"
  if [[ ! -d "${CONSOLE_DEST}" ]]; then
    log "no console install at ${CONSOLE_DEST}; skipping console refresh"
    return 0
  fi
  if [[ ! -f "${meta}" ]]; then
    log "WARNING: no console build metadata at ${meta}; console left unchanged"
    log "  build on a networked machine first: scripts/stage-console-build.sh"
    return 0
  fi

  # Next.js serves a prebuilt .next with the host's own `next` package, so a
  # version skew fails at request time rather than at build time. Refuse instead
  # of installing a build this host cannot serve.
  local built_next installed_next built_url
  built_next="$(json_field "${meta}" next_version)"
  built_url="$(json_field "${meta}" gateway_url)"
  installed_next="$(json_field "${CONSOLE_DEST}/node_modules/next/package.json" version)"
  [[ -n "${built_next}" && -n "${installed_next}" ]] ||
    die "cannot determine console next versions (build=${built_next:-?} host=${installed_next:-?})"
  [[ "${built_next}" == "${installed_next}" ]] ||
    die "console build used next ${built_next} but ${CONSOLE_DEST} serves with ${installed_next}; rebuild against ${installed_next}"
  # NEXT_PUBLIC_* is inlined at build time, so a wrong value silently repoints
  # every browser at another gateway.
  [[ "${built_url}" == "${CONSOLE_GATEWAY_URL}" ]] ||
    die "console build targets gateway ${built_url}, expected ${CONSOLE_GATEWAY_URL}"

  local console_backup="${CONSOLE_DEST}/.next.bak-${STAMP}"
  log "backing up console build → ${console_backup}"
  rm -rf "${console_backup}"
  rsync -a --exclude 'cache/' "${CONSOLE_DEST}/.next/" "${console_backup}/"

  log "installing console build ${built_next} (next=${installed_next}, gateway=${built_url})"
  rsync -a --delete --exclude 'cache/' "${src}/.next/" "${CONSOLE_DEST}/.next/"
  # Tests are not shipped: the host has no vitest/typescript and never runs them.
  rsync -a --delete \
    --exclude '*.test.ts' --exclude '*.test.tsx' \
    "${src}/src/" "${CONSOLE_DEST}/src/"
  [[ -d "${src}/public" ]] && rsync -a "${src}/public/" "${CONSOLE_DEST}/public/"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${CONSOLE_DEST}"

  log "restarting yonder-console"
  local ok=0
  if systemctl restart yonder-console; then
    for attempt in $(seq 1 15); do
      sleep 2
      if curl -fsS --max-time 4 -o /dev/null "${CONSOLE_URL}"; then
        log "console healthy after ${attempt} check(s)"
        ok=1
        break
      fi
    done
  fi
  if [[ "${ok}" -ne 1 ]]; then
    log "console did not come up; restoring previous build"
    journalctl -u yonder-console -n 40 --no-pager | tail -40 || true
    rsync -a --delete --exclude 'cache/' "${console_backup}/" "${CONSOLE_DEST}/.next/"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${CONSOLE_DEST}"
    systemctl restart yonder-console || true
    die "console refresh failed and was rolled back; gateway/worker remain on the new build"
  fi
  mapfile -t stale_console < <(ls -1dt "${CONSOLE_DEST}"/.next.bak-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)))
  for old in "${stale_console[@]:-}"; do
    [[ -n "${old}" ]] || continue
    log "pruning old console build ${old}"
    rm -rf "${old}"
  done
}

refresh_console

systemctl is-active yonder-gateway yonder-worker yonder-llama-cpp yonder-console
curl -fsS http://127.0.0.1:8080/health
echo
mapfile -t stale_backups < <(ls -1dt "${DEST}".bak-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)))
for old in "${stale_backups[@]:-}"; do
  [[ -n "${old}" ]] || continue
  log "pruning old backup ${old}"
  rm -rf "${old}"
done

log "deploy complete. Next: Gateway canaries, then operator alias promotion."
log "rollback: systemctl stop yonder-gateway yonder-worker; rm -rf ${DEST}; mv ${BACKUP} ${DEST}; systemctl start yonder-gateway yonder-worker"
