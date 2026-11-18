# Model Manifests

Models are defined as YAML manifests, not application code.

See `config/models/yonder-chat-small.example.yaml` for the initial shape.

## Current status

* Manifests live under `config/models/*.yaml` (skip `*.example.yaml`).
* Validation covers missing files (non-mock), unknown providers, context length,
  GPU allocation, quantization, and duplicate IDs.
* Operator validate/load/drain/unload flows are available via
  `/api/admin/models*` and `/api/admin/deployments*`.
* Mac-local mock lane uses `runtime.provider: mock` and skips weight-path presence.
* `Qwen3-Embedding-4B`, `Qwen3-Embedding-8B`, and `Qwen3-Reranker-4B`
  currently exist in the model-bundle inventory as safetensors. They have no
  loadable Gateway manifests or route aliases yet. Create derived GGUFs, run
  profile-specific embedding/rerank canaries, retain the logs, and only then
  promote reviewed manifests. Inventory presence is not readiness.

## Validation targets

Manifest validation must catch:

* Missing model files
* Unsupported runtime
* Invalid context length
* Impossible GPU allocation
* Unknown provider
* Duplicate model ID
* Invalid quantization settings
* `placement_limits` present but empty, or default `runtime.context_length`
  exceeding the default placement mode's `max_context_length`

## Placement limits (1-GPU / 2-GPU context caps)

Schedulable llama.cpp manifests declare hard context caps under
`requirements.placement_limits`:

* `single_gpu.max_context_length` / `two_gpu.max_context_length` — operator
  ceilings for lab V100s (32 GB / 64 GB pooled). Use `0` to forbid a mode
  (Coder Next forbids single-GPU).
* Optional per-mode `minimum_gpu_memory_gb` overrides the global floor for
  free-VRAM admission.
* `runtime.context_length` is the **default** selected at load; it must be ≤ the
  max for the default tensor-parallel / placement mode.

Load API accepts optional `context_length`; Gateway validates against the selected
mode's cap, then persists the resolved value on the deployment so the worker can
set `--ctx-size`. Caps are `research_estimate` until host-measured — see
`docs/gpu-model-scheduler.md`.

## Aliases

Clients use stable aliases such as `yonder-chat`. Aliases point at deployments and
can move from a V100 deployment to an A100 deployment without client changes.

## Current lane

* Lab with `llama_cpp.enabled`: `yonder-chat` points at the Qwen3.6 GGUF deployment.
* Mac-local / tests (`llama_cpp.enabled=false`): `yonder-chat` may point at
  `yonder-chat-mock-primary`. That is an explicit mock deployment, not fabricated
  GPU readiness.

## GGUF fleet (llama.cpp schedulable)

| Manifest ID | Bundle folder | Default placement |
| --- | --- | --- |
| `yonder-qwen36-35b-a3b-q4` | `Qwen3.6-35B-A3B-GGUF` | single GPU (`tensor_parallel_size: 1`), default ctx **65536** (`evidence: measured` on ai1) |
| `yonder-qwen36-27b-q4` | `Qwen3.6-27B-GGUF` | single GPU (dense UD-Q4_K_XL; caps measured) |
| `yonder-bonsai-27b-ternary` | `Ternary-Bonsai-27B-GGUF` | single GPU; **PrismML llama.cpp** via `runtime.binary_path`; caps **measured** 1×/2× **262144** |
| `yonder-qwen3-coder-next-q4` | `Qwen3-Coder-Next-GGUF` | two GPU (`tensor_parallel_size: 2`) |
| `yonder-gemma4-12b-it-qat-gguf` | `gemma-4-12B-it-qat-gguf` | single GPU (caps `research_estimate`) |
| `yonder-gemma4-26b-a4b-it-qat-gguf` | `gemma-4-26B-A4B-it-qat-gguf` | single GPU (caps `research_estimate`) |
| `yonder-gemma4-31b-it-qat-gguf` | `gemma-4-31B-it-qat-gguf` | single GPU |

Download the QAT/GGUF wave (not full safetensors) with
`scripts/download-models-wave-gguf-v100.sh`. Prefer GGUF staging on APFS, then
rsync to `/Volumes/yonder/model-bundles` and lab NFS.

Stable client aliases beyond `yonder-chat` (`yonder-coder`, `yonder-gemma-qat`,
and any new Gemma/Qwen aliases) are operator-enabled after a successful placed
load — they are not auto-registered.

## Diffusers fleet (image / video)

| Manifest ID | Bundle folder | Notes |
| --- | --- | --- |
| `yonder-z-image-turbo` | `Z-Image-Turbo` | `provider: diffusers`, family `z-image` |
| `yonder-sdxl-turbo` | `sdxl-turbo-fp16` (+ `sdxl-turbo-diffusers-config`) | single-file SDXL; offline config tree required |
| `yonder-cogvideox-5b` | `CogVideoX-5b` | short-clip canaries on 32 GB V100 |
| `yonder-wan22-ti2v-5b` | `Wan2.2-TI2V-5B-Diffusers` | short-clip canaries on 32 GB V100 |

Lab enablement: `diffusers.enabled` + `diffusers_runtime.enabled` in
`config/environments/lab-multinode.yaml`. Install CUDA torch/Diffusers into
**each** worker venv with `scripts/install-diffusers-runtime-worker.sh`
(ai1 wrapper: `install-diffusers-runtime-ai1.sh`). Shared NFS model bundles
under `/srv/yonder-storage/model-bundles` are fine read-only on ai3/ai7;
`diffusers_runtime.output_root` is **`/var/lib/yonder/generated`** (per-host
writable). Loopback canary: `scripts/canary-diffusers-model.sh` (set
`DIFFUSERS_PYTHON` to the worker venv on remote hosts).

**Operator rule:** Diffusers and llama.cpp cannot share a GPU on the same
worker. Unload one runtime before placing the other on that GPU.

**Fleet rule:** New models/abilities are not lab-ready until every registered
worker can load them (see AGENTS.md lab worker parity). Diffusers image path
is validated on `worker-ai1`, `worker-ai3`, and `worker-ai7`.

## Safetensors (non-Diffusers)

Folder discovery still reports safetensors trees with honest
`model_format` / `runtime_candidate` values. Console Load for scheduled chat
deployments remains gated on `runtime.provider=llama-cpp` and an existing GGUF
path. Safetensors chat models stay discoverable but **not loadable** by
llama.cpp until a converted/packaged runtime is approved.
