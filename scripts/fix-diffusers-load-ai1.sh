#!/usr/bin/env bash
# Hot-fix Diffusers load/generate path on ai1 and smoke txt2img via Gateway.
# Requires sudo (YubiKey). On ai1:
#   sudo bash /opt/yonder/src/scripts/fix-diffusers-load-ai1.sh
set -euo pipefail

SRC_ROOT="${SRC_ROOT:-/opt/yonder/src}"
WORKER_DST=/opt/yonder/venv-worker/lib/python3.12/site-packages/app/api/lifecycle.py
GATEWAY_DST=/opt/yonder/venv/lib/python3.12/site-packages/app/services/lifecycle.py
DIFFUSERS_MGR_DST=/opt/yonder/venv-worker/lib/python3.12/site-packages/app/runtime/diffusers_manager.py
DROPIN_DIR=/etc/systemd/system/yonder-worker.service.d
DROPIN="${DROPIN_DIR}/storage-paths.conf"

[[ "$(id -u)" -eq 0 ]] || { echo "Must run as root (sudo)"; exit 1; }
[[ -f "${SRC_ROOT}/apps/worker/app/api/lifecycle.py" ]] || {
  echo "Missing ${SRC_ROOT}/apps/worker/app/api/lifecycle.py — sync the repo first"; exit 1;
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"

echo "[fix] patching worker lifecycle (advertise Diffusers base_url)"
cp -a "${WORKER_DST}" "${WORKER_DST}.bak-${stamp}"
cp "${SRC_ROOT}/apps/worker/app/api/lifecycle.py" "${WORKER_DST}"
chown yonder:yonder "${WORKER_DST}"
chmod 640 "${WORKER_DST}"

if [[ -f "${SRC_ROOT}/apps/worker/app/runtime/diffusers_manager.py" ]]; then
  echo "[fix] patching worker Diffusers manager"
  cp -a "${DIFFUSERS_MGR_DST}" "${DIFFUSERS_MGR_DST}.bak-${stamp}"
  cp "${SRC_ROOT}/apps/worker/app/runtime/diffusers_manager.py" "${DIFFUSERS_MGR_DST}"
  chown yonder:yonder "${DIFFUSERS_MGR_DST}"
  chmod 640 "${DIFFUSERS_MGR_DST}"
fi

echo "[fix] patching gateway lifecycle (pass runtime.family)"
cp -a "${GATEWAY_DST}" "${GATEWAY_DST}.bak-${stamp}"
cp "${SRC_ROOT}/apps/gateway/app/services/lifecycle.py" "${GATEWAY_DST}"
chown yonder:yonder "${GATEWAY_DST}"
chmod 640 "${GATEWAY_DST}"

echo "[fix] ensuring Diffusers packages are readable by yonder"
chown -R yonder:yonder /opt/yonder/venv-worker
chmod -R u+rwX /opt/yonder/venv-worker

echo "[fix] systemd ReadWritePaths for /srv/yonder-storage"
mkdir -p "${DROPIN_DIR}"
cat > "${DROPIN}" <<'EOF'
[Service]
# Diffusers output_root + model-bundle staging under ProtectSystem=strict.
ReadWritePaths=/var/lib/yonder /var/log/yonder /etc/yonder /srv/yonder-storage
EOF
mkdir -p /srv/yonder-storage/generated
chown yonder:yonder /srv/yonder-storage/generated
chmod 0755 /srv/yonder-storage/generated
systemctl daemon-reload

echo "[fix] unload orphaned Diffusers deployment if present"
curl -fsS -X POST http://127.0.0.1:8090/v1/lifecycle/unload \
  -H 'content-type: application/json' \
  -d '{"deployment_id":"yonder-sdxl-turbo-wai1-g1","operation":"unload","runtime_configuration":{"provider":"diffusers"}}' \
  >/dev/null 2>&1 || true

echo "[fix] restart worker + gateway"
systemctl restart yonder-worker yonder-gateway
for i in $(seq 1 30); do
  sleep 1
  if curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1 \
    && curl -fsS --max-time 3 http://127.0.0.1:8090/health >/dev/null 2>&1; then
    echo "[fix] healthy after ${i}s"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "[fix] services did not become healthy"; exit 1
  fi
done

python3 - <<'PY'
import json, time, urllib.error, urllib.request

def req(method, path, body=None, timeout=600):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8080" + path, data=data, method=method
    )
    if body is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        return exc.code, payload

print("[fix] Loading yonder-sdxl-turbo on worker-ai1 GPU 1…")
code, out = req(
    "POST",
    "/api/admin/deployments/load",
    {
        "model_definition_id": "yonder-sdxl-turbo",
        "worker_id": "worker-ai1",
        "placement_mode": "single_gpu",
        "gpu_indexes": [1],
    },
)
print("[fix] load", code)
dep = out.get("deployment") or {}
dep_id = dep.get("id")
if not dep_id:
    raise SystemExit(json.dumps(out, indent=2)[:4000])

for _ in range(90):
    time.sleep(2)
    _, deployments = req("GET", "/api/admin/deployments")
    match = next(
        (item for item in deployments.get("deployments", []) if item.get("id") == dep_id),
        None,
    )
    status = (match or {}).get("status")
    print("[fix] deployment", status)
    if status in {"ready", "loaded"}:
        break
    if status in {"failed", "error", "unavailable"}:
        raise SystemExit(json.dumps(match, indent=2)[:4000])
else:
    raise SystemExit("timed out waiting for ready")

print("[fix] Creating txt2img job via Gateway…")
code, job_out = req(
    "POST",
    "/v1/generation/jobs",
    {
        "mode": "txt2img",
        "prompt": "a red apple on a wooden table, soft daylight, photorealistic",
        "steps": 4,
        "guidance": 0.0,
        "aspect_ratio": "1:1",
        "model": "yonder-image",
    },
)
print("[fix] create", code)
job = job_out.get("job") or {}
job_id = job.get("id") or job.get("job_id")
if not job_id:
    raise SystemExit(json.dumps(job_out, indent=2)[:4000])

for _ in range(90):
    time.sleep(2)
    _, payload = req("GET", f"/v1/generation/jobs/{job_id}")
    job = payload.get("job") or payload
    status = job.get("status")
    print("[fix] job", status, job.get("error") or "")
    if status in {"succeeded", "completed", "ready", "done"}:
        print(json.dumps(job, indent=2)[:4000])
        print("[fix] PASS")
        break
    if status in {"failed", "error", "cancelled"}:
        raise SystemExit(json.dumps(job, indent=2)[:4000])
else:
    raise SystemExit("job timed out")
PY
