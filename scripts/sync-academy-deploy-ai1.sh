#!/usr/bin/env bash
# Operator-assisted: sync Academy durable + curriculum pack to ai1 and deploy.
# Prerequisites: YubiKey ready; wheels built under /tmp/yonder-wheels; console
# staged via scripts/stage-console-build.sh.
#
# Console-only fast path (UI/CSS only, no Python wheel changes):
#   NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080 ./scripts/stage-console-build.sh
#   rsync … ai1:/opt/yonder/src   # include apps/console/.next
#   ssh ai1 'sudo …/deploy-ai1-verified-runtime.sh /opt/yonder/src'
# The deploy script refreshes /opt/yonder/console/.next when yonder-build-meta.json
# is present; still prefer the full script so gateway/worker stay aligned when
# curriculum YAML or gateway code changed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELS="${WHEELS:-/tmp/yonder-wheels}"
REMOTE_SRC="${REMOTE_SRC:-ai1:/opt/yonder/src}"
REMOTE_WHEELS="${REMOTE_WHEELS:-ai1:/opt/yonder/yonder-reinstall-wheels}"

echo "Touch YubiKey when SSH prompts…"
rsync -az \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude 'apps/console/node_modules' \
  --exclude 'vendor/' \
  --exclude '__pycache__' \
  --exclude '.next/cache' \
  -e 'ssh -A -o ForwardAgent=yes' \
  "${ROOT}/" "${REMOTE_SRC}/"

scp -o ForwardAgent=yes \
  "${WHEELS}"/yonder_contracts-*.whl \
  "${WHEELS}"/yonder_configuration-*.whl \
  "${WHEELS}"/yonder_telemetry-*.whl \
  "${WHEELS}"/yonder_gateway-*.whl \
  "${REMOTE_WHEELS}/"

echo "Synced. On ai1 run (sudo/YubiKey):"
echo "  sudo /opt/yonder/src/scripts/deploy-ai1-verified-runtime.sh /opt/yonder/src"
echo "Then smoke:"
echo "  curl -sS http://127.0.0.1:8080/v1/lab/curriculum | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d[\"tracks\"]), sum(len(t[\"lessons\"]) for t in d[\"tracks\"]))'"
echo "  # restart gateway and re-check challenge/workbench survival"
