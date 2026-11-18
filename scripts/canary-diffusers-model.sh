#!/usr/bin/env bash
# Direct Diffusers load + one-shot generation canary before Gateway promotion.
# Prefer the CUDA venv under /srv/yonder-storage/venvs/diffusers-cuda when the
# system worker venv lacks torch (air-gapped hosts install from wheel caches).
set -euo pipefail

MODEL_PATH="${1:-}"
FAMILY="${2:-}"
PROFILE="${3:-image}"  # image | video
GPU="${4:-1}"
PROMPT="${5:-a small red cube on a white table, studio light}"
STEPS="${STEPS:-4}"
FRAMES="${FRAMES:-17}"
HEIGHT="${HEIGHT:-512}"
WIDTH="${WIDTH:-512}"
OUT_ROOT="${OUT_ROOT:-/var/lib/yonder/generated}"
# Prefer the systemd worker venv (installed on every lab host). Fall back to the
# shared storage canary venv when present and executable (ai1).
if [[ -z "${DIFFUSERS_PYTHON:-}" ]]; then
  if [[ -x /opt/yonder/venv-worker/bin/python ]]; then
    PY=/opt/yonder/venv-worker/bin/python
  elif [[ -x /opt/yonder/venv/bin/python ]]; then
    PY=/opt/yonder/venv/bin/python
  else
    VENV="${DIFFUSERS_VENV:-/srv/yonder-storage/venvs/diffusers-cuda}"
    PY="${VENV}/bin/python"
  fi
else
  PY="${DIFFUSERS_PYTHON}"
fi
VENV="${DIFFUSERS_VENV:-/srv/yonder-storage/venvs/diffusers-cuda}"
LOGDIR="${LOGDIR:-/tmp/yonder-canaries}"
YONDER_SRC="${YONDER_SRC:-/opt/yonder/src}"

die() { printf '[diffusers-canary] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[diffusers-canary] %s\n' "$*"; }

[[ -n "${MODEL_PATH}" && -n "${FAMILY}" ]] \
  || die "Usage: $0 <model-path> <family> [profile] [gpu] [prompt]"
[[ -e "${MODEL_PATH}" ]] || die "Missing model path: ${MODEL_PATH}"
[[ -x "${PY}" ]] || die "Missing python: ${PY} (set DIFFUSERS_VENV or DIFFUSERS_PYTHON)"
[[ -d "${YONDER_SRC}/apps/worker" ]] || die "Missing worker tree: ${YONDER_SRC}/apps/worker"
mkdir -p "${LOGDIR}" "${OUT_ROOT}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOGDIR}/diffusers-${FAMILY}-${STAMP}.log"
JOB_ID="canary-${FAMILY}-${STAMP}"
export CUDA_VISIBLE_DEVICES="${GPU}"
export PYTHONPATH="${YONDER_SRC}/apps/worker:${YONDER_SRC}/packages/configuration:${YONDER_SRC}/packages/contracts:${YONDER_SRC}/packages/telemetry${PYTHONPATH:+:$PYTHONPATH}"

log "profile=${PROFILE} family=${FAMILY} gpu=${GPU} model=${MODEL_PATH}"
log "log=${LOG}"

"${PY}" - "${MODEL_PATH}" "${FAMILY}" "${PROFILE}" "${PROMPT}" "${STEPS}" "${FRAMES}" "${HEIGHT}" "${WIDTH}" "${OUT_ROOT}" "${JOB_ID}" <<'PY' 2>&1 | tee "${LOG}"
import sys
import time
from pathlib import Path

model_path = Path(sys.argv[1])
family = sys.argv[2]
profile = sys.argv[3]
prompt = sys.argv[4]
steps = int(sys.argv[5])
frames = int(sys.argv[6])
height = int(sys.argv[7])
width = int(sys.argv[8])
out_root = Path(sys.argv[9])
job_id = sys.argv[10]

import importlib.util
import os
import types

try:
    import torch
    import diffusers
except ImportError as exc:
    raise SystemExit(f"missing runtime packages: {exc}") from exc


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Load by file path to avoid worker/package __init__ pulling httpx/llama.cpp.
yonder_src = Path(os.environ.get("YONDER_SRC", "/opt/yonder/src"))
cfg_pkg = types.ModuleType("yonder_configuration")
cfg_pkg.__path__ = [
    str(yonder_src / "packages/configuration/yonder_configuration")
]
sys.modules["yonder_configuration"] = cfg_pkg
models_mod = _load_module(
    "yonder_configuration.models",
    yonder_src / "packages/configuration/yonder_configuration/models.py",
)
cfg_pkg.models = models_mod
manager_mod = _load_module(
    "diffusers_manager_canary",
    yonder_src / "apps/worker/app/runtime/diffusers_manager.py",
)
DiffusersWorkerRuntimeConfig = models_mod.DiffusersWorkerRuntimeConfig
DiffusersRuntimeManager = manager_mod.DiffusersRuntimeManager

print(
    f"torch={torch.__version__} cuda={torch.cuda.is_available()} "
    f"diffusers={diffusers.__version__}"
)
if not torch.cuda.is_available():
    raise SystemExit("torch.cuda.is_available() is False")
print(f"device0={torch.cuda.get_device_name(0)}")

for name in (
    "CogVideoXPipeline",
    "WanPipeline",
    "StableDiffusionXLPipeline",
    "DiffusionPipeline",
):
    print(f"symbol {name}={'yes' if hasattr(diffusers, name) else 'no'}")

cfg = DiffusersWorkerRuntimeConfig(
    enabled=True,
    dry_run=False,
    allowed_model_roots=[
        Path("/srv/yonder-storage/model-bundles"),
        model_path if model_path.is_dir() else model_path.parent,
    ],
    output_root=out_root,
    device="cuda",
    dtype="float16",
)
manager = DiffusersRuntimeManager(cfg)
deployment_id = f"canary-{family}"
loaded = manager.load(
    deployment_id=deployment_id,
    model_definition_id=f"canary-{family}",
    runtime_configuration={
        "profile": profile,
        "family": family,
        "model_path": str(model_path),
    },
    gpu_assignments=[0],  # remapped via CUDA_VISIBLE_DEVICES
)
print("load", loaded)
if loaded.get("status") != "ready":
    raise SystemExit(f"load not ready: {loaded}")

mode = "txt2img" if profile == "image" else "txt2video"
params = {"steps": steps, "guidance": 3.5}
if profile == "video":
    params["num_frames"] = frames
    params["height"] = height
    params["width"] = width
elif family == "sdxl":
    params["height"] = height
    params["width"] = width
    params["guidance"] = 0.0
    params["steps"] = max(steps, 1)

job = manager.create_job(
    job_id=job_id,
    deployment_id=deployment_id,
    mode=mode,
    prompt=prompt,
    negative_prompt="",
    params=params,
)
print("job_accepted", job)
deadline = time.time() + 900
final = None
while time.time() < deadline:
    final = manager.get_job(job["job_id"])
    status = (final or {}).get("status")
    print("poll", status)
    if status in {"succeeded", "failed", "cancelled"}:
        break
    time.sleep(2)
print("final", final)
manager.unload(deployment_id)
if not final or final.get("status") != "succeeded":
    raise SystemExit(f"canary failed: {final}")
assets = final.get("assets") or []
if not assets:
    raise SystemExit("canary succeeded without assets")
artifact = manager.resolve_artifact(assets[0]["artifact_id"])
if not artifact or not Path(artifact["path"]).is_file():
    raise SystemExit(f"artifact missing: {artifact}")
size = Path(artifact["path"]).stat().st_size
if size < 1000:
    raise SystemExit(f"artifact too small ({size} bytes): {artifact['path']}")
print(
    f"[diffusers-canary] PASS profile={profile} family={family} "
    f"artifact={artifact['path']} bytes={size}"
)
PY

grep -q '\[diffusers-canary\] PASS' "${LOG}" || die "PASS marker missing; see ${LOG}"
log "PASS profile=${PROFILE} family=${FAMILY}"
