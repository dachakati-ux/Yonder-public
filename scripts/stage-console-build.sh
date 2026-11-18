#!/usr/bin/env bash
# Build the console for an air-gapped host and record the toolchain used.
#
# ai1 has no npm registry access, so the console runs a prebuilt .next produced
# on a networked machine. Next.js serves that output with whatever `next` is
# installed on the host, so a build/runtime version mismatch surfaces as runtime
# 500s rather than a build error. The metadata written here lets the deploy
# script refuse a mismatched build instead of shipping a broken console.
#
# NEXT_PUBLIC_* values are inlined into client bundles at build time, so the
# gateway URL is recorded too: a rebuild must not silently repoint browsers.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONSOLE_DIR="${REPO_ROOT}/apps/console"
GATEWAY_URL="${NEXT_PUBLIC_YONDER_GATEWAY_URL:-http://127.0.0.1:8080}"

die() { printf '[stage-console] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[stage-console] %s\n' "$*"; }

if [[ -d "${REPO_ROOT}/.tools/node/bin" ]]; then
  PATH="${REPO_ROOT}/.tools/node/bin:${PATH}"
  export PATH
fi
command -v npm >/dev/null 2>&1 || die "npm not found on PATH"
[[ -d "${CONSOLE_DIR}/node_modules/next" ]] || die "console dependencies missing; run npm install in ${CONSOLE_DIR}"

cd "${CONSOLE_DIR}"
log "building with NEXT_PUBLIC_YONDER_GATEWAY_URL=${GATEWAY_URL}"
NODE_ENV=production \
NEXT_TELEMETRY_DISABLED=1 \
NEXT_PUBLIC_YONDER_GATEWAY_URL="${GATEWAY_URL}" \
  npm run build

next_version="$(node -e "process.stdout.write(require('next/package.json').version)")"
react_version="$(node -e "process.stdout.write(require('react/package.json').version)")"
build_id="$(cat .next/BUILD_ID)"

node -e '
const fs = require("fs");
fs.writeFileSync(".next/yonder-build-meta.json", JSON.stringify({
  next_version: process.argv[1],
  react_version: process.argv[2],
  build_id: process.argv[3],
  gateway_url: process.argv[4],
  built_at: new Date().toISOString(),
}, null, 2) + "\n");
' "${next_version}" "${react_version}" "${build_id}" "${GATEWAY_URL}"

log "recorded build metadata:"
cat .next/yonder-build-meta.json
log "next: sync the repo to the target host, then run scripts/deploy-ai1-verified-runtime.sh"
