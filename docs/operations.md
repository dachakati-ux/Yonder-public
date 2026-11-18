# Operations

## Local process model (Mac)

```bash
make bootstrap
make gateway   # 127.0.0.1:8080
make worker    # 127.0.0.1:8090
make console   # 127.0.0.1:3000
```

Open `/hardware` and `/operations` in the console. Hardware stays empty until the worker
heartbeats; it will not invent nodes or GPU values.

## Console layout

The operator pages use one compact pattern so they stay readable as hosts and
models are added:

* A **stat strip** across the top of `/models`, `/hardware`, and `/operations`
  summarises the fleet at a glance. A tile shows `not_reported` rather than `0`
  when no worker has supplied the value.
* **`/hardware`** lists one collapsed card per node showing hostname, CPU,
  memory, and a GPU summary. Byte values are human-readable (`16.0 GB`) in
  binary multiples, matching `free -h` and `nvidia-smi`. Expanding a card (click
  or keyboard, via `details`/`summary`) reveals every metric the worker
  reported: host, per-GPU detail, storage mounts, network interfaces, runtime
  summary, and collector notes. Filter and expand-all controls appear once at
  least one node is registered.
* **`/models`** leads with a placement console: pick a schedulable definition, a
  worker, one or two GPUs from tiles that show VRAM usage and any current
  occupant, then read the **preflight safeguards** panel before loading. Each
  check is `ok`, `warn`, or `block`. A `block` disables the load button; a `warn`
  requires an explicit confirmation. The preflight mirrors admission and fails
  closed: a selected GPU that never reported measured free VRAM, total VRAM, or
  its own index blocks the load, and a GPU discovery status other than
  `available` blocks it too, because the Gateway discards that inventory. The
  VRAM check is per device — each selected GPU must hold the manifest's
  `minimum_gpu_memory_gb` plus the runtime reserve (1 GiB by default, set
  server-side). The Gateway remains the authority and can still refuse with
  `GPU_PLACEMENT_CONFLICT` or `INVENTORY_UNAVAILABLE`.
* **`/operations`** keeps workers, providers, routes, deployments, and capacity
  as compact rows that expand to their controls, with request/failure/audit
  ledgers collapsed until opened.

Usage bars are only drawn when the underlying metric was reported; an unreported
metric renders an explicit `not_reported` marker instead of an empty bar.

## Lab worker parity

Registered lab workers today: `worker-ai1`, `worker-ai3`, `worker-ai7`.

When a new model family or ability lands, treat **fleet enablement** as part of
the same assignment:

1. Runtime deps installed on **each** worker host (not only the bring-up node).
2. Weights readable on each host (shared NFS bundles are OK if paths match).
3. Writable local dirs for generated artifacts / caches when NFS is `ro`.
4. Smoke or canary on each worker before calling the ability lab-ready.

ai1-only spikes are fine for first evidence; close parity before “done.”

Diffusers (2026-08-02): installed on ai1/ai3/ai7 via
`scripts/install-diffusers-runtime-worker.sh`. Outputs use
`/var/lib/yonder/generated` because ai3/ai7 mount `/srv/yonder-storage` read-only.

## Lab aliases (operator-set)

Aliases live in Gateway durable state (not `lab-multinode.yaml`). After READY
loads exist, point stable names without inventing readiness:

```bash
GATEWAY_URL=http://127.0.0.1:8080 ./scripts/lab-set-stable-aliases.sh
```

Convention:

| Alias | Intent |
| --- | --- |
| `yonder-chat` | Primary Qwen3.6 chat |
| `yonder-nemotron-4b` | Default Nemotron (prefer ai3) |
| `yonder-nemotron-4b-ai7` | Dedicated ai7 Nemotron (no flip during canaries) |
| `yonder-qwen35-9b` | Qwen3.5 9B |
| `yonder-coder` | Qwen3-Coder-Next (two-GPU only) |
| `yonder-embed-4b` / `yonder-embed-8b` / `yonder-rerank-4b` | Embed/rerank |

Internal coder desk (Phase A→G pipeline, write locked by default): see [`docs/coder.md`](./coder.md).

MCP (IDE server + Gateway client): lab/dev env files enable Gateway client +
Serena on loopback; see [`docs/mcp.md`](./mcp.md). Units:
`yonder-mcp.service`, `yonder-serena-mcp.service`.

Assistant canary (tools/jail, no invented VIP scores):

```bash
./scripts/canary-coder-assistant.sh
./scripts/canary-coder-pipeline.sh
```

## Two-GPU coder canary (ai1 sidecar)

If static `yonder-llama-cpp` still holds ~11 GiB/GPU, stop it before a coder load:

```bash
STOP_SIDECAR=1 ./scripts/canary-coder-two-gpu.sh worker-ai1 0,1
```

Prefer worker-managed chat before permanently disabling the sidecar
(`docs/gpu-model-scheduler.md`).

## Context / VRAM measurement

```bash
./scripts/measure-context-vram.sh <model_id> <worker_id> single_gpu 0 2048,8192,16384
```

Promote manifest caps only from the resulting TSV (`evidence: measured`).

## Chat context meter + auto-condense

Lab `context_window` (see `config/environments/lab-multinode.yaml`) enables
Gateway soft pressure on `/v1/chat/completions`:

* Chat toolbar shows `Context ~N% · estimate` (char heuristic) or
  `Context N% · provider` when llama.cpp reports usage, else
  `Context not_reported`.
* Crossing `soft_ratio` (default 0.70 of deployment `context_length`) triggers
  an automatic summary of older turns; the condensed transcript is persisted
  when the console passes `conversation_id`.
* Keep busy prompts under the soft ratio when possible; near-ceiling fills still
  fail closed at runtime over `n_ctx`.

## Reaching the lab console from a workstation

> **Lab only — pending security review (YON-7).** Host firewall (YAKL) still
> blocks direct VPN access to TCP 3000/8080. Until owners approve ACLs, the
> interim **approved** access method is SSH local-forward via the corporate jump
> host (not a firewall change).

The lab hosts sit behind a jump host. Until those ACLs are opened (YON-7), reach
the console with SSH local port forwarding:

```bash
# With SSH host aliases configured:
ssh -N -L 3000:127.0.0.1:3000 -L 8080:127.0.0.1:8080 ai1

# Without aliases (replace USER with your corporate username):
ssh -N \
  -L 3000:127.0.0.1:3000 \
  -L 8080:127.0.0.1:8080 \
  -J USER@bastion.example.internal \
  USER@control-plane.example.internal
```

Then browse `http://127.0.0.1:3000`. Both forwards are required: the console is
served on 3000, and the browser calls the gateway directly on 8080.

Lab Forgejo (company tooling git, **loopback only** on `127.0.0.1:3001`) uses
the same tunnel pattern — see [`docs/forgejo-lab.md`](./forgejo-lab.md):

```bash
ssh -N -L 3001:127.0.0.1:3001 ai1
# browse http://127.0.0.1:3001
```

The gateway URL is inlined into the console bundle at build time via
`NEXT_PUBLIC_YONDER_GATEWAY_URL`, so it must match the address the *browser*
uses, not the address the server uses. If the Hardware page renders but shows no
nodes while `GET /api/admin/hardware` succeeds on the host, the bundle is almost
certainly baked to an address the browser cannot reach.

Useful probes:

* `GET /health`
* `GET /ready`
* `GET /version`
* `GET /metrics`
* `GET /v1/models`
* `POST /v1/chat/completions`
* Worker: `GET /v1/hardware`
* Worker: `GET /v1/status`

Model load timing (for NFS vs local comparisons):

* Deployment logs include `Load timing: … duration_ms=… worker_duration_ms=…`
* Structured journal events: `deployment.load.completed` / `runtime.load.completed`
* Prometheus: `yonder_model_loads_total`, `yonder_model_load_duration_seconds`
  (labels: `worker_id`, `provider`, `outcome`)
* Audit metadata on `deployment.loaded` / `deployment.load_failed` includes
  `duration_ms`, `artifact_bytes`, `model_path`, `started_at`, `finished_at`

### Phase 2 chat smoke (mock provider)

```bash
curl -s http://127.0.0.1:8080/v1/models | jq .

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"yonder-chat","messages":[{"role":"user","content":"hello"}],"stream":false}' | jq .

curl -N http://127.0.0.1:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"yonder-chat","messages":[{"role":"user","content":"hello"}],"stream":true}'
```

Unknown aliases return structured `404` with `UNKNOWN_MODEL`.

## Development security warning

When `security.mode=development`, gateway and worker print a prominent startup
warning. The console skeleton also displays a development banner.

## Operator actions

Dangerous controls in the console require confirmation:

* Drain deployment
* Unload model
* Disable route
* Maintenance mode

Administrative actions emit operational audit events / request ledger entries.

## Hardening checks

```bash
make test-all
make migrate
make verify
```

`make verify` is the full local and CI gate: configuration validation, Ruff, all
Python suites, migration upgrade verification, console unit tests, and a production
console build.

Graceful stop: interrupt gateway/worker with SIGINT/SIGTERM. Gateway drains
in-flight work briefly and persists state; worker notifies disconnect.

Admission knobs live under `admission` in environment YAML / `YONDER_ADMISSION_*`
env overlays (active limit, queue depth, queue timeout, inference timeout).

## Mac caveat

Without Docker/Node installed, Compose and console live reload are optional.
Python gateway/worker via `uv` is the supported local path.
