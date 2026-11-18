# GPU model scheduler — host rollout notes

## Goal

Gateway-controlled GGUF (and Diffusers) placement on ai1/ai3/ai7, with
worker-managed runtimes. Soft multi-lane sharing allows up to
`capacity.max_lanes_per_gpu` (default **3**) occupying deployments per physical
GPU index when **measured** free VRAM still fits. EnsureReady may auto-load a
needed model into a free lane, prefer-evict idle Diffusers, and demote the sole
chat lane under pressure. Operator **locks** and sole-chat protection prevent
silent kicks; missing VRAM metrics still fail closed.

## Same-host multi-GPU safety policy

Yonder supports one- or two-GPU placement only within one worker/server. A
`GpuPlacement` contains exactly one `worker_id`; `two_gpu` requires exactly two
unique indexes on that worker. Cross-host model sharding is intentionally out of
scope until a separately secured and measured RPC design exists.

The runtime follows llama.cpp's documented conservative path:

* Set `CUDA_VISIBLE_DEVICES` to exactly the selected physical indexes. Do not
  represent an excluded GPU as a zero in `--tensor-split`; llama.cpp can still
  allocate CUDA context memory on such devices.
* Use `--split-mode layer` by default. It is the stable pipeline mode. Yonder does
  not expose experimental `tensor` mode because llama.cpp disables auto-fit in
  that mode and documents startup/prefill OOM as an operator-managed risk.
* Set `--tensor-split` from live free VRAM after subtracting configured reserve,
  preserving the selected-GPU order after CUDA remapping. This avoids an unsafe
  equal split when another process consumes uneven memory.
* Gateway admission checks measured `memory_available_bytes`, not installed
  capacity, and reserves 1 GiB per selected GPU by default. Missing free-memory
  metrics fail closed.
* The worker repeats the VRAM check immediately before `llama-server` starts,
  using `nvidia-smi`. This catches stale heartbeats and operator-managed
  processes unknown to Yonder's deployment registry.

`minimum_gpu_memory_gb` remains a per-selected-GPU requirement. For example, a
two-GPU manifest declaring 24 GiB requires at least 25 GiB free on each card with
the default 1 GiB reserve. This conservative rule prevents a 47 GiB model from
being admitted onto two cards that each have only ~21 GiB free.

## Load-time context length + placement hard caps

Operators pick context length at load (Models console presets:
`2048 … 262144`, filtered by mode hard max, always including the measured
`placement_limits` max so absolute safe windows are selectable). Gateway writes
the resolved value into `deployment.runtime_configuration.context_length`; the
worker passes it as llama.cpp `--ctx-size` (it must not ignore the deployment
and fall back to a fixed manager default).

Manifest `requirements.placement_limits` adds a **fail-closed** gate on top of
live free-VRAM admission:

```yaml
requirements:
  minimum_gpu_memory_gb: 12
  placement_limits:
    single_gpu:
      max_context_length: 32768   # 0 / omit => mode forbidden
      minimum_gpu_memory_gb: 12  # optional mode-specific floor
    two_gpu:
      max_context_length: 65536
```

Caps below are **measured lab V100 operator limits** (Gateway load ladders +
settle peaks; chat also fill-smoked). Native model windows may be larger; we
clamp to the highest READY ctx with ~≥2 GiB free. Do not invent measured peak
VRAM in docs.

| Model | 1× max safe ctx | 2× max safe ctx | Notes |
| --- | ---: | ---: | --- |
| Nemotron-3-Nano-4B q8_0 | **262144** (measured safe) | **262144** (measured) | 2026-08-08 small climb from 64K: 1× OK through 262144 (~8766 MiB). Fill OK. 2× OK @ 262144 (~5480+5798 MiB). |
| Qwen3.5-9B q8_0 | **262144** (measured safe) | **262144** (measured) | 1× OK through 262144 (~17392 MiB). Fill OK. 2× OK @ 262144 (~9442+10258 MiB). |
| Gemma-4-12B QAT q4_0 | **262144** (measured safe) | **262144** (measured) | 1× OK through 262144 (~13264 MiB). Fill OK. 2× OK @ 262144 (~7748+7882 MiB). |
| Qwen3-Embedding-4B | **163840** (measured safe) | **163840** (measured) | Load OK through 163840 (~27938 MiB); rejected 196608. 2× OK @ 163840. |
| Qwen3-Embedding-8B | **131072** (measured safe) | **131072** (measured) | Load OK 163840 (~31536 MiB, <2 GiB free) → safe 131072 (~26896 MiB). Rejected 196608. 2× OK @ 131072. |
| Qwen3-Reranker-4B | **163840** (measured safe) | **163840** (measured) | Load OK through 163840 (~27938 MiB); rejected 196608. 2× OK @ 163840. |
| Qwen3.6-35B-A3B UD-Q4_K_XL | **262144** (measured safe) | **262144** (measured safe) | 1× OK through 262144 (~26864 MiB). 2× climb 2026-08-08: OK through 262144 (~14810+14306 MiB). Fill OK at 2× 262144. |
| Qwen3.6-27B UD-Q4_K_XL | **163840** (measured safe) | **262144** (measured safe) | 1× safe 163840 (~28734 MiB; load OK 196608, fail 229376). 2× OK through 262144 (~18950+18376 MiB). Fill OK at 2× 262144. |
| Gemma-4-26B-A4B QAT q4_0 | **262144** (measured safe) | **262144** (measured safe) | 1× OK through 262144 (~21778 MiB). 2× OK through 262144 (~12374+11718 MiB). Fill OK at 2× 262144. |
| Gemma-4-31B QAT q4_0 | **98304** (measured safe) | **262144** (measured safe) | 1× safe 98304 (~30032 MiB; load OK 131072, crash 163840). 2× OK through 262144 (~23124+22280 MiB, ≥2 GiB free/GPU). Fill OK at 2× 262144. |
| Qwen3-Coder-Next UD-Q4_K_XL | blocked (`0`) | **262144** (measured safe) | 2× load OK through 262144 (~28916+27498 MiB). Fill OK. |

**Research → measured:** run Gateway-mediated measurements (do not invent peaks):

```bash
# On ai1 / via tunnel — starts low and climbs until load rejects or OOM
GATEWAY_URL=http://127.0.0.1:8080 \
  ./scripts/measure-context-vram.sh \
  yonder-nemotron-3-nano-4b worker-ai3 single_gpu 0 \
  2048,8192,16384,32768,65536
```

Each successful row records measured `memory_used` MiB from admin hardware
heartbeats. After review, set `max_context_length` to the highest passing
preset and flip `evidence: measured`. TSV evidence lands under
`/tmp/yonder-ctx-measure-*.tsv`.

### Fleet model gauntlet

Gateway-only harness for context ladders + task-fit probes. Hosts are treated
as equivalent: **assign different models per host** rather than replicating
every model on every box.

#### Fast-track campaign (rough data tonight)

Three concurrent runners, shared durable `--outdir`, stress deferred:

| Wave | Host | Models |
| --- | --- | --- |
| `ai1-big` | `worker-ai1` | Gemma 31B, Qwen3.6-35B, Coder (2-GPU) |
| `ai3-chat` | `worker-ai3` | Nemotron 4B, Qwen3.5-9B |
| `ai7-retrieval` | `worker-ai7` | embed 4B/8B, rerank 4B (`single_gpu`) |

```bash
OUT=/srv/yonder-storage/yonder/gauntlet/20260731-fast
mkdir -p "$OUT"
# three tmux panes on ai1 (or any host that reaches Gateway):
GATEWAY_URL=http://127.0.0.1:8080 ./scripts/bench-fleet-gauntlet.sh \
  --wave ai1-big --ladder fast --pins first --skip-stress --outdir "$OUT"
GATEWAY_URL=http://127.0.0.1:8080 ./scripts/bench-fleet-gauntlet.sh \
  --wave ai3-chat --ladder fast --pins first --skip-stress --outdir "$OUT"
GATEWAY_URL=http://127.0.0.1:8080 ./scripts/bench-fleet-gauntlet.sh \
  --wave ai7-retrieval --ladder fast --pins first --skip-stress --outdir "$OUT"
```

* `--ladder fast` — ctx `2048,8192,32768` (+ higher two_gpu steps when allowed)
* `--pins first` — only `[0]` / `[0,1]` (ai1 `2,3` free for manual ops)
* `--skip-stress` — context + inference only when `--phase all`
* Resume: re-run the same command; skips completed steps via `state.json` /
  `runs.jsonl`. `--no-resume` forces a fresh pass.
* Crash: `SIGINT`/`SIGTERM` unloads tracked deployments and sweeps orphans on
  that process’s `--hosts`.

Watch: Models UI + `tail -f "$OUT/runs.jsonl"`.

Tomorrow: `--ladder full --pins all` on promising models; then stress.

#### Flags / outputs

* `--phase context|inference|stress|all`
* `--mode single_gpu|two_gpu|both`
* `--wave ai1-big|ai3-chat|ai7-retrieval`
* `--dry-run` — print matrix without loads
* `--models <id,id,...>` — optional subset

Emits under `--outdir`:

* `runs.jsonl` — atomic step records (flock-safe for parallel writers)
* `state.json` — `last_ok_ctx`, completed keys, rejected ladder points
* `summary.tsv` / `failures.tsv` / `README.md`

Missing VRAM or ledger fields stay `not_reported`. Manifests are never
auto-edited — promote `max_context_length` + `evidence: measured` only after
human review.

Primary references:

* <https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md>
* <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>

The pinned ai1 build (`b10167`) was also checked directly and supports
`--device`, `--split-mode {none,layer,row,tensor}`, and `--tensor-split`.

### PrismML second runtime (Bonsai)

Ternary / 1-bit Bonsai GGUFs need PrismML's llama.cpp fork (custom Q2_0_g128 /
hybrid-attention kernels). Lab install path:

`/opt/yonder/runtimes/llama.cpp-prismml-cuda-sm70`

Manifests that need it set `runtime.binary_path` to that tree's `llama-server`.
The worker default remains `llama.cpp-b10167-cuda-sm70` for all other GGUFs.
Build helper: `scripts/build-prismml-llama-sm70-ai1.sh`. Sync to workers with
`scripts/sync-prismml-runtime-to-workers.sh`.

## Packaging (YON-6b)

1. Stage artifacts on the pack build machine:
   - `vendor/cuda/cuda_*_linux.run` (CUDA 12.6.3 toolkit runfile)
   - `vendor/runtimes/llama.cpp-b10167-cuda-sm70/` (copy from ai1
     `/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70`)
2. `make offline-pack` stages them under `payload/cuda` and `payload/runtimes`.
3. On each GPU worker after base install:

```bash
sudo bash install-llama-runtime.sh /media/yonder-pack/yonder-offline-rhel8-amd64
```

4. Enable worker runtime via `/etc/yonder/worker.env` using
   `payload/env/llama-cpp-runtime.env.example`.

## ai1 migration (static sidecar → worker manager)

Current lab escape hatch: `yonder-llama-cpp.service` on `127.0.0.1:8000`.

Preferred path:

1. Keep the static unit **stopped** once worker-managed loads succeed.
2. Set `YONDER_LLAMA_CPP_RUNTIME__ENABLED=true` on `worker-ai1`.
3. Gateway loads with `placement_mode` + `gpu_indexes`; worker starts loopback
   processes on ports ≥8100 and returns `base_url`.
4. Optional: `yonder-llama-cpp@.service` remains packaged for operator-managed
   named instances; scheduled loads use the worker process manager by default.

## ai3 enablement

Do **not** enable remote loads until model weights are reachable on ai3:

* **preferred:** NFSv4 mount of ai1 `/srv/yonder-storage` (see
  `scripts/provision-ai1-nfs-export.sh` + `scripts/provision-worker-nfs-client.sh`), or
* synced subset of the GGUF trees on local SSD.

Then install the llama runtime pack, enable `llama_cpp_runtime`, and place loads
with `worker_id=worker-ai3`.

Remote inference still follows the Gateway-only rule. Worker-managed
`llama-server` processes bind to `127.0.0.1` on ai3. The worker advertises
`http://worker-01.example.internal:8090/v1/runtime/<deployment-id>` to the Gateway;
the worker's allowlisted reverse proxy forwards only health, model discovery,
chat, embedding, and rerank routes to the corresponding loopback process.
Dynamic llama.cpp ports (8100+) must not be opened to the network.

For the measured ai3 rollout, stage the current source/wheels, pinned sm_70
runtime, CUDA 12 runtime libraries, and manifest-identical GGUF paths, then run:

```bash
sudo /opt/yonder/src/scripts/deploy-worker-verified-runtime.sh \
  /opt/yonder/src \
  /opt/yonder/yonder-ai3-stage
```

The deploy helper verifies checksums and shared libraries before enabling the
manager, preserves the existing worker identity/Gateway endpoint, and confirms
`runtime=unified` plus the runtime proxy route before reporting success.

## ai7 enablement

Same pattern as ai3: provision `/srv/yonder-storage`, copy the checksummed
verified stage, run `deploy-worker-verified-runtime.sh`, then place loads with
`worker_id=worker-ai7`. Measured 2026-07-31: Nemotron 4B single-GPU load
`yonder-nemotron-3-nano-4b-wai7-g0` ready; Gateway chat canary returned `ai7-ok`.

Ensure `/opt/cuda-12.6` is traversable by `yonder` (`chmod 0755`) and that
`yonder-worker.service.d/runtime-paths.conf` adds `ReadOnlyPaths` for the
runtime + CUDA trees under `ProtectSystem=strict`.

## ai5 (abandoned)

Replaced by **ai7**. Empty / failed GPU discovery must still fail closed
(`inventory_unavailable` / `worker_maintenance`). Never invent VRAM capacity.
Do not re-add `worker-ai5` to the allowlist.

## Chat demotion ladder (lab)

When EnsureReady must free VRAM and the sole `yonder-chat` lane is protected,
it demotes chat down `capacity.chat_demotion_ladder` (largest → smallest)
instead of bare-evicting the last chat. Lab ladder (manifest IDs on fleet):

1. `yonder-qwen36-35b-a3b-q4` (~24 GiB)
2. `yonder-gemma4-31b-it-qat-gguf` (~20 GiB)
3. `yonder-qwen36-27b-q4` (~22 GiB)
4. `yonder-gemma4-26b-a4b-it-qat-gguf` (~20 GiB)
5. `yonder-gemma4-12b-it-qat-gguf` (~16 GiB)
6. `yonder-qwen35-9b` (~12 GiB)
7. `yonder-nemotron-3-nano-4b` (~6 GiB)

Missing ladder entries are skipped. Demotion unloads first, credits/refreshes
VRAM inventory, then loads the next smaller definition and rebinds `yonder-chat`.
