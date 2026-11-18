#!/usr/bin/env bash
# Install a verified worker runtime payload on an air-gapped Yonder GPU worker.
# The Gateway remains the only control plane. llama.cpp stays on worker loopback;
# Gateway inference is carried through the worker's fixed :8090 runtime proxy.
set -euo pipefail

SRC="${1:-}"
STAGE="${2:-}"
DEST="${DEST:-/opt/yonder/repo}"
VENV_PY="${VENV_PY:-/opt/yonder/venv/bin/python}"
UV="${UV:-/opt/yonder/uv/uv}"
ENV_FILE="${ENV_FILE:-/etc/yonder/worker.env}"
SERVICE_USER="${SERVICE_USER:-yonder}"
SERVICE_GROUP="${SERVICE_GROUP:-yonder}"
RUNTIME_NAME="llama.cpp-b10167-cuda-sm70"
RUNTIME_DEST="/opt/yonder/runtimes/${RUNTIME_NAME}"
CUDA_DEST="/opt/cuda-12.6/lib64"
MODEL_DEST="/srv/yonder-storage/model-bundles"
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"

log() { printf '[deploy-worker-runtime] %s\n' "$*"; }
die() { printf '[deploy-worker-runtime] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Must run as root (sudo)"
[[ -d "${SRC}/apps/worker" ]] || die "Invalid source checkout: ${SRC}"
[[ -d "${STAGE}/runtime/${RUNTIME_NAME}" ]] || die "Runtime payload missing"
[[ -d "${STAGE}/cuda/lib64" ]] || die "CUDA shared-library payload missing"
[[ -d "${STAGE}/models/model-bundles" ]] || die "Model payload missing"
[[ -d "${STAGE}/wheels" ]] || die "Wheel payload missing"
[[ -f "${STAGE}/SHA256SUMS" ]] || die "Payload checksum manifest missing"
[[ -x "${UV}" ]] || die "Bundled uv missing: ${UV}"
[[ -x "${VENV_PY}" ]] || die "Worker venv missing: ${VENV_PY}"
[[ -f "${ENV_FILE}" ]] || die "Worker environment missing: ${ENV_FILE}"
# Refuse to install models onto the OS root. provision-*-storage.sh must
# mount the dedicated SSD at /srv/yonder-storage first, otherwise rsync lands
# on the root filesystem and disappears when the SSD is later mounted over it.
mountpoint -q /srv/yonder-storage \
  || die "/srv/yonder-storage is not a mountpoint; run provision-ai3-storage.sh or provision-ai7-storage.sh first"

log "verifying staged runtime, CUDA, model, and wheel checksums"
(cd "${STAGE}" && sha256sum -c SHA256SUMS)

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="${DEST}.bak-${STAMP}"
ENV_BACKUP="${ENV_FILE}.bak-${STAMP}"
log "backing up source and worker environment"
cp -a "${DEST}" "${BACKUP}"
cp -a "${ENV_FILE}" "${ENV_BACKUP}"

log "syncing worker source"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'dist/' --exclude '__pycache__' --exclude '.env*' \
  "${SRC}/" "${DEST}/"

log "installing current worker/configuration/contracts/telemetry wheels offline"
"${UV}" pip install \
  --python "${VENV_PY}" \
  --offline --no-index --no-deps \
  --find-links "${STAGE}/wheels" \
  --reinstall-package yonder-contracts \
  --reinstall-package yonder-configuration \
  --reinstall-package yonder-telemetry \
  --reinstall-package yonder-worker \
  "${STAGE}"/wheels/yonder_contracts-*.whl \
  "${STAGE}"/wheels/yonder_configuration-*.whl \
  "${STAGE}"/wheels/yonder_telemetry-*.whl \
  "${STAGE}"/wheels/yonder_worker-*.whl
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${VENV_PY%/bin/python}"
chmod -R u+rwX "${VENV_PY%/bin/python}"

log "installing pinned sm_70 llama.cpp runtime and CUDA 12 runtime libraries"
mkdir -p "${RUNTIME_DEST}" "${CUDA_DEST}"
rsync -a --delete "${STAGE}/runtime/${RUNTIME_NAME}/" "${RUNTIME_DEST}/"
rsync -a "${STAGE}/cuda/lib64/" "${CUDA_DEST}/"
# yonder must traverse /opt/cuda-12.6 (mkdir/rsync may leave it 0750 root:root).
# Verify as the service user — root ldd succeeds even when yonder cannot open the dir.
chmod 0755 /opt/cuda-12.6
chown -R root:"${SERVICE_GROUP}" /opt/cuda-12.6
chmod -R a+rX "${RUNTIME_DEST}" "${CUDA_DEST}" /opt/cuda-12.6
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" /opt/yonder/runtimes
if ! runuser -u "${SERVICE_USER}" -- test -r "${CUDA_DEST}/libcudart.so.12"; then
  die "CUDA libs not readable by ${SERVICE_USER} at ${CUDA_DEST}"
fi
# Ensure ProtectSystem=strict workers can still see runtime + model roots.
mkdir -p /etc/systemd/system/yonder-worker.service.d
cat > /etc/systemd/system/yonder-worker.service.d/runtime-paths.conf <<'EOF'
[Service]
ReadOnlyPaths=/opt/yonder/runtimes /opt/cuda-12.6
ReadWritePaths=/srv/yonder-storage /var/lib/yonder /var/log/yonder /etc/yonder
EOF
systemctl daemon-reload

log "installing verified model subset at manifest-identical paths"
mkdir -p "${MODEL_DEST}"
rsync -a "${STAGE}/models/model-bundles/" "${MODEL_DEST}/"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" /srv/yonder-storage
find "${MODEL_DEST}" -type d -exec chmod 0755 {} \;
find "${MODEL_DEST}" -type f -exec chmod 0644 {} \;

if command -v restorecon >/dev/null 2>&1; then
  restorecon -Rv /opt/yonder/runtimes /opt/cuda-12.6 /srv/yonder-storage \
    >/dev/null 2>&1 || true
fi

upsert_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "${ENV_FILE}" > "${tmp}" || true
  printf '%s=%s\n' "${key}" "${value}" >> "${tmp}"
  cat "${tmp}" > "${ENV_FILE}"
  rm -f "${tmp}"
}

log "enabling loopback-only worker runtime; :8090 proxies Gateway inference"
upsert_env YONDER_LLAMA_CPP_RUNTIME__ENABLED true
upsert_env YONDER_LLAMA_CPP_RUNTIME__DRY_RUN false
upsert_env YONDER_LLAMA_CPP_RUNTIME__BINARY_PATH \
  "${RUNTIME_DEST}/bin/llama-server"
upsert_env YONDER_LLAMA_CPP_RUNTIME__LIBRARY_PATH "${CUDA_DEST}"
upsert_env YONDER_LLAMA_CPP_RUNTIME__LISTEN_HOST 127.0.0.1
upsert_env YONDER_LLAMA_CPP_RUNTIME__BASE_PORT 8100
upsert_env YONDER_LLAMA_CPP_RUNTIME__VRAM_RESERVE_GB 1
upsert_env YONDER_LLAMA_CPP_RUNTIME__MULTI_GPU_SPLIT_MODE layer
upsert_env YONDER_LLAMA_CPP_RUNTIME__ALLOWED_MODEL_ROOTS \
  '["/srv/yonder-storage/model-bundles"]'
chown root:"${SERVICE_GROUP}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

log "verifying binary dependencies before worker restart"
LD_LIBRARY_PATH="${RUNTIME_DEST}/bin:${CUDA_DEST}" \
  ldd "${RUNTIME_DEST}/bin/llama-server" |
  tee /tmp/yonder-worker-llama-ldd.txt
if grep -q 'not found' /tmp/yonder-worker-llama-ldd.txt; then
  die "llama-server has missing shared libraries; worker was not restarted"
fi

log "restarting yonder-worker"
if ! systemctl restart yonder-worker; then
  journalctl -u yonder-worker -n 80 --no-pager || true
  die "worker restart failed; restore ${ENV_BACKUP} and ${BACKUP}"
fi

for attempt in $(seq 1 20); do
  sleep 2
  if curl -fsS --max-time 4 http://127.0.0.1:8090/health >/dev/null; then
    break
  fi
  [[ "${attempt}" -lt 20 ]] || {
    journalctl -u yonder-worker -n 80 --no-pager || true
    die "worker did not become healthy"
  }
done

status="$(curl -fsS http://127.0.0.1:8090/v1/lifecycle/status)"
printf '%s\n' "${status}"
grep -q '"runtime":"unified"' <<<"${status}" ||
  die "worker is healthy but managed runtime is not enabled"
curl -fsS http://127.0.0.1:8090/openapi.json |
  grep -q '/v1/runtime/{deployment_id}/{runtime_path}' ||
  die "runtime proxy route is missing"
nvidia-smi --query-gpu=index,name,memory.free,memory.total --format=csv,noheader

mapfile -t stale < <(
  ls -1dt "${DEST}".bak-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1))
)
for old in "${stale[@]:-}"; do
  [[ -n "${old}" ]] && rm -rf "${old}"
done

log "worker runtime deploy complete"
log "rollback env: cp ${ENV_BACKUP} ${ENV_FILE}"
log "rollback source: rm -rf ${DEST}; mv ${BACKUP} ${DEST}"
