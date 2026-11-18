#!/usr/bin/env bash
# Yonder Coder LIVE canary — proves the agent loop against a real model.
#
# Runs ONE eval task end-to-end through the gateway using whatever model the
# coder alias resolves to (llama.cpp on ai1, or LM Studio locally). Nothing is
# mocked: the run only completes if the model produced a passing change and
# both verdicts approved.
#
# Prerequisites:
#   - Gateway running with coder.enabled=true and coder.write_unlock=true
#   - Coder alias READY (yonder-coder on ai1, or an attach alias for LM Studio)
#   - GATEWAY_URL reachable
#
# Usage:
#   GATEWAY_URL=http://127.0.0.1:8080 \
#   CODER_SUBJECT=canary-operator \
#   scripts/canary-coder-live.sh [task-id]
#
# On trusted_ingress gateways set CODER_SUBJECT so the harness sends the
# X-Yonder-Trusted-Subject / capabilities headers. In development mode the
# mock principal already holds coder.use, so leave it empty.
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
TASK_ID="${1:-bug-subtract-add}"
TIMEOUT="${CANARY_TIMEOUT:-900}"
SUBJECT="${CODER_SUBJECT:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Yonder Coder live canary =="
echo "gateway:  $GATEWAY_URL"
echo "task:     $TASK_ID"
echo

echo "-- coder status --"
curl -sS "$GATEWAY_URL/v1/coder/status" | python3 -m json.tool || {
  echo "Gateway not reachable at $GATEWAY_URL" >&2
  exit 1
}
echo

echo "-- coder alias readiness (must be ready before a live run) --"
curl -sS "$GATEWAY_URL/v1/models" | python3 -c '
import json,sys
data=json.load(sys.stdin)
ids=[m.get("id") for m in data.get("data",[])]
print("aliases:", ids)
' || true
echo

echo "-- running eval task via harness --"
exec python3 "$REPO_ROOT/scripts/coder-eval.py" \
  --gateway "$GATEWAY_URL" \
  --task "$TASK_ID" \
  --timeout "$TIMEOUT" \
  --subject "$SUBJECT" \
  --json "$REPO_ROOT/data/canary-coder-live-${TASK_ID}.json"
