#!/usr/bin/env bash
# Local coder pipeline canary — clone sample_repo, run staged pipeline.
# Does NOT flip write_unlock in lab config. Pass WRITE_UNLOCK=1 to exercise
# apply/check/dual_verify/push against a temporary Gateway env override.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
SAMPLE="${ROOT}/config/lab/coder-evals/sample_repo"

echo "== coder status =="
curl -fsS "${GATEWAY_URL}/v1/coder/status" | python3 -m json.tool

echo "== clone sample_repo =="
CLONE=$(curl -fsS -X POST "${GATEWAY_URL}/v1/coder/workspaces/clone" \
  -H 'content-type: application/json' \
  -d "{\"source\":\"${SAMPLE}\",\"label\":\"canary-sample\"}")
echo "${CLONE}" | python3 -m json.tool
WID=$(echo "${CLONE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace"]["id"])')

echo "== create run =="
RUN=$(curl -fsS -X POST "${GATEWAY_URL}/v1/coder/runs" \
  -H 'content-type: application/json' \
  -d "{\"workspace_id\":\"${WID}\",\"task\":\"Fix add() so tests pass\",\"check_command\":\"python -m pytest\"}")
echo "${RUN}" | python3 -m json.tool
RID=$(echo "${RUN}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run"]["id"])')

echo "== advance auto =="
ADV=$(curl -fsS -X POST "${GATEWAY_URL}/v1/coder/runs/${RID}/advance" \
  -H 'content-type: application/json' \
  -d '{"auto":true}')
echo "${ADV}" | python3 -m json.tool

STATUS=$(echo "${ADV}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run"]["status"])')
STAGE=$(echo "${ADV}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run"]["stage"])')
echo "result status=${STATUS} stage=${STAGE}"
echo "Record real outcome in WORKLOG.md. Do not invent READY or pass rates."
