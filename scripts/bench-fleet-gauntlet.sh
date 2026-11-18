#!/usr/bin/env bash
# Fleet model gauntlet — Gateway-orchestrated measurement battery.
#
# Prefer measured numbers only. Missing hardware/ledger fields are recorded as
# not_reported. Does not edit model manifests; prints suggested YAML snippets
# for operator review after the run.
#
# Fast-track parallel campaign (3 tmux panes, shared outdir):
#   OUT=/srv/yonder-storage/yonder/gauntlet/20260731-fast
#   mkdir -p "$OUT"
#   GATEWAY_URL=http://127.0.0.1:8080 ./scripts/bench-fleet-gauntlet.sh \
#     --wave ai1-big --ladder fast --pins first --skip-stress --outdir "$OUT"
#   # panes 2–3: --wave ai3-chat / --wave ai7-retrieval
#
# Dry-run a wave:
#   ./scripts/bench-fleet-gauntlet.sh --wave ai3-chat --ladder fast --dry-run
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"

exec python3 "${ROOT}/scripts/bench_fleet_gauntlet.py" "$@"
