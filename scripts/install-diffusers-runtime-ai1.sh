#!/usr/bin/env bash
# Compatibility wrapper — prefer scripts/install-diffusers-runtime-worker.sh
# for any lab worker (ai1/ai3/ai7).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${ROOT}/install-diffusers-runtime-worker.sh" "$@"
