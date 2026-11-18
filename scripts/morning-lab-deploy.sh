#!/usr/bin/env bash
# Morning one-shot: push staged local work to the lab (ai1 + ai3 + ai7).
# Run from the Mac checkout after YubiKey is ready. Builds are expected under
# /tmp/yonder-wheels and apps/console/.next (see stage steps below).
#
# Prereq (done offline / night before):
#   NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080 ./scripts/stage-console-build.sh
#   python -m build --wheel --no-isolation --outdir /tmp/yonder-wheels \
#     packages/contracts packages/configuration packages/telemetry apps/gateway apps/worker
#
# Usage:
#   bash scripts/morning-lab-deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELS="${WHEELS:-/tmp/yonder-wheels}"
REMOTE_SRC="${REMOTE_SRC:-ai1:/opt/yonder/src}"
REMOTE_WHEELS_AI1="${REMOTE_WHEELS_AI1:-ai1:/opt/yonder/yonder-reinstall-wheels}"
REMOTE_WORKER_WHEELS="${REMOTE_WORKER_WHEELS:-/opt/yonder/yonder-worker-wheels}"

die() { printf '[morning-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[morning-deploy] %s\n' "$*"; }

[[ -d "${ROOT}/apps/gateway" ]] || die "run from yonder checkout"
[[ -f "${ROOT}/apps/console/.next/BUILD_ID" ]] || die "console not staged; run stage-console-build.sh"
[[ -f "${WHEELS}/yonder_gateway-0.1.0-py3-none-any.whl" ]] || die "missing gateway wheel in ${WHEELS}"
[[ -f "${WHEELS}/yonder_worker-0.1.0-py3-none-any.whl" ]] || die "missing worker wheel in ${WHEELS}"
[[ -f "${ROOT}/scripts/install-worker-wheels-lab.sh" ]] || die "missing install-worker-wheels-lab.sh"

log "Touch YubiKey as prompted…"
rsync -az \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude 'apps/console/node_modules' --exclude 'vendor/' \
  --exclude '__pycache__' --exclude '.next/cache' \
  -e 'ssh -A -o ForwardAgent=yes' \
  "${ROOT}/" "${REMOTE_SRC}/"

scp -o ForwardAgent=yes \
  "${WHEELS}"/yonder_contracts-*.whl \
  "${WHEELS}"/yonder_configuration-*.whl \
  "${WHEELS}"/yonder_telemetry-*.whl \
  "${WHEELS}"/yonder_gateway-*.whl \
  "${WHEELS}"/yonder_worker-*.whl \
  "${REMOTE_WHEELS_AI1}/"

log "deploying ai1 verified runtime (gateway + console)"
ssh -A -o ForwardAgent=yes ai1 \
  "sudo /opt/yonder/src/scripts/deploy-ai1-verified-runtime.sh /opt/yonder/src"

log "installing worker wheels on ai1 venv-worker + ai3/ai7 venv"
ssh -A -o ForwardAgent=yes ai1 "mkdir -p ${REMOTE_WORKER_WHEELS}"
scp -o ForwardAgent=yes \
  "${WHEELS}"/yonder_contracts-*.whl \
  "${WHEELS}"/yonder_configuration-*.whl \
  "${WHEELS}"/yonder_telemetry-*.whl \
  "${WHEELS}"/yonder_worker-*.whl \
  "${ROOT}/scripts/install-worker-wheels-lab.sh" \
  "ai1:${REMOTE_WORKER_WHEELS}/"

ssh -A -o ForwardAgent=yes -t ai1 \
  "chmod +x ${REMOTE_WORKER_WHEELS}/install-worker-wheels-lab.sh && \
   sudo --preserve-env=SSH_AUTH_SOCK SYSTEM_VENV=/opt/yonder/venv-worker \
     ${REMOTE_WORKER_WHEELS}/install-worker-wheels-lab.sh ${REMOTE_WORKER_WHEELS}"

for h in ai3 ai7; do
  log "worker wheels → ${h}"
  ssh -A -o ForwardAgent=yes "${h}" "mkdir -p ${REMOTE_WORKER_WHEELS}"
  scp -o ForwardAgent=yes \
    "${WHEELS}"/yonder_contracts-*.whl \
    "${WHEELS}"/yonder_configuration-*.whl \
    "${WHEELS}"/yonder_telemetry-*.whl \
    "${WHEELS}"/yonder_worker-*.whl \
    "${ROOT}/scripts/install-worker-wheels-lab.sh" \
    "${h}:${REMOTE_WORKER_WHEELS}/"
  ssh -A -o ForwardAgent=yes -t "${h}" \
    "chmod +x ${REMOTE_WORKER_WHEELS}/install-worker-wheels-lab.sh && \
     sudo --preserve-env=SSH_AUTH_SOCK \
       ${REMOTE_WORKER_WHEELS}/install-worker-wheels-lab.sh ${REMOTE_WORKER_WHEELS}"
done

log "optional image smoke (EnsureReady)"
ssh -A -o ForwardAgent=yes ai1 'bash -s' <<'SMOKE' || log "WARN: image smoke failed — check Studio"
set -euo pipefail
CREATE=$(curl -fsS -X POST http://127.0.0.1:8080/v1/generation/jobs \
  -H 'content-type: application/json' \
  -d '{"mode":"txt2img","prompt":"morning deploy smoke cyan Y","aspect_ratio":"1:1","steps":4,"guidance":7.5}')
JOB=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['job']['id'])" "$CREATE")
echo "job=$JOB"
for i in $(seq 1 90); do
  LIVE=$(curl -fsS "http://127.0.0.1:8080/v1/generation/jobs/$JOB")
  ST=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['job']['status'])" "$LIVE")
  echo "poll $i $ST"
  case "$ST" in succeeded) echo PASS; exit 0 ;; failed|error|cancelled|rejected|not_supported) echo FAIL; exit 1 ;; esac
  sleep 5
done
exit 1
SMOKE

log "optional video canary (skip if busy): ssh ai1 'bash /opt/yonder/src/scripts/canary-video-gateway.sh'"
log "done. Hard-refresh console (Cmd+Shift+R)."
