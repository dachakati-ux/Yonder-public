#!/usr/bin/env bash
# One-command Academy smoke against a running gateway + console (default: local
# loopback on ai1 after SSH). Does not invent readiness — only HTTP shapes.
set -euo pipefail
GW="${YONDER_GATEWAY_URL:-http://127.0.0.1:8080}"
CONSOLE="${YONDER_CONSOLE_URL:-http://127.0.0.1:3000}"

echo "[academy-smoke] gateway=${GW} console=${CONSOLE}"

curl -fsS "${GW}/health" >/dev/null
python3 - <<PY
import json, os, urllib.request
gw = os.environ.get("YONDER_GATEWAY_URL", "${GW}")
cur = json.load(urllib.request.urlopen(f"{gw}/v1/lab/curriculum", timeout=10))
tracks = cur["tracks"]
lessons = sum(len(t["lessons"]) for t in tracks)
print(f"curriculum {len(tracks)} tracks {lessons} lessons")
assert len(tracks) >= 8
assert lessons >= 40
missions = json.load(urllib.request.urlopen(f"{gw}/v1/lab/missions", timeout=10))["missions"]
print(f"missions {len(missions)}")
assert len(missions) >= 4
for m in missions:
    detail = json.load(urllib.request.urlopen(f"{gw}/v1/lab/missions/{m['id']}", timeout=10))
    assert detail["mission"]["steps"]
cap = json.load(urllib.request.urlopen(f"{gw}/v1/lab/capacity", timeout=10))
assert "aliases" in cap
cohorts = json.load(urllib.request.urlopen(f"{gw}/v1/lab/cohorts", timeout=10))
assert "cohorts" in cohorts
print("capacity aliases", len(cap.get("aliases") or []))
print("cohorts", len(cohorts["cohorts"]))
PY

for path in \
  /academy \
  /academy/missions \
  /academy/cohorts \
  /academy/capacity \
  /academy/workbench \
  /academy/prompts \
  /academy/sandbox \
  /academy/instructor
do
  code=$(curl -sS -o /dev/null -w "%{http_code}" "${CONSOLE}${path}")
  echo "${code} ${path}"
  [[ "${code}" == "200" ]]
done

echo "[academy-smoke] OK"
