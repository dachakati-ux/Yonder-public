# Architecture

Yonder is an internal AI platform control plane. The first deployment target is a
single NVIDIA V100 worker, but the architecture treats that host as one of many
future workers.

## Control plane shape

```text
Internal user or application
            │
            ▼
Corporate security and approved ingress
            │
            ▼
       Yonder Gateway
            │
    ┌───────┼────────┐
    ▼       ▼        ▼
  V100    Future    Future
 worker   V100s     A100s
```

## Services

| Service | Role |
| --- | --- |
| `apps/gateway` | Stable OpenAI-compatible API, routing, tools/RAG/embeddings/generation jobs, operator APIs |
| `apps/worker` | Hardware inventory, heartbeats, llama.cpp runtime manager (or lifecycle stubs), generation `not_supported` stub |
| `apps/console` | Chat, Academy, Generation Studio (product jobs), Training Lab, operator UI; calls gateway only |

## Shared packages

| Package | Role |
| --- | --- |
| `yonder-contracts` | Principals, errors, hardware metrics, chat/tools/embeddings/generation contracts, model bundles |
| `yonder-configuration` | Typed layered settings and startup validation |
| `yonder-telemetry` | Structured JSON logs and Prometheus helpers |

## Hard rules

1. Clients never talk to runtimes or workers directly.
2. Metrics that cannot be collected are marked with explicit availability states.
3. Authentication is out of scope; only normalized trusted context is accepted.
4. Adding an A100 server later must be registration + manifests + alias repointing.

## Phase status

* Phase 0: repository, docs, configuration, service skeletons — complete for Mac-local bootstrap.
* Phase 1: worker hardware discovery — complete for Mac host; V100 discovery deferred.
* Phase 2: OpenAI-compatible gateway foundation with mock streaming provider — complete.
* Phase 3: real V100 runtime — live on ai1. Gateway `llama-cpp` adapter + CUDA
  `sm_70` lane (`yonder-chat`); GPU model scheduler (GGUF placement + worker
  runtime manager) lands in-repo; offline-pack staging of toolkit/runtime is
  optional via `install-llama-runtime.sh`.
* Phase 4: Yonder Chat UI against gateway — complete for mock lane.
* Phase 5: model lifecycle (manifests, load/drain/unload, alias repoint) — complete
  for mock lane; GGUF placement validation + explicit unload conflicts for lab.
* Phase 6: hardware/operations UI — complete (gateway-mediated, no fabricated metrics).
* Phase 7: hardening — complete for Mac-local foundation (queues/timeouts, durable state, shutdown, migrations, integration/e2e tests).
* Pre-GPU expansion: SQLAlchemy store, conversation retention, trusted-ingress header mapping, worker lifecycle stubs, capacity ops UI — complete for Mac-local.
* Pre-GPU completion: default SQLite, durable audit/ledger, CI, experimental lanes, worker trust policy stub — complete.
* Pre-GPU reliability: hardened worker reporting, admission/timeout cleanup,
  lifecycle failure persistence, provider conformance, structured host evidence,
  recovery tooling, and console CI — complete.
* Full AI deployment foundation (host-independent): offline deploy hardening,
  modality contracts, Gateway tools/RAG/embeddings/structured services,
  durable generation jobs/artifacts, Studio product job API, offline model-bundle
  tooling — complete as **mock / not_supported** until V100 evidence enables a runtime.

## Platform APIs (Gateway-only)

| Surface | Status |
| --- | --- |
| `/v1/chat/completions` | Live mock provider |
| `/v1/embeddings` | Live mock-embeddings provider (deterministic vectors) |
| `/v1/platform/tools*` | Allowlisted Gateway tool executor |
| `/v1/platform/rag*` | Persistent lexical RAG store + retrieve |
| `/v1/platform/schemas` | Versioned structured-output schemas |
| `/v1/generation/*` | Durable jobs/uploads/artifacts; provider returns `not_supported` |
| `/v1/speech/*` | Gateway-mediated STT/TTS (`transcribe`, `synthesize`, audio artifacts); fail closed until worker speech is READY/mock |
| `/v1/coder/*` | Internal coder status, principal-scoped workspaces, read-only path-jailed tools; write unlock off by default |
| `/v1/mcp/*` | Config-gated MCP client (status/servers/tools/call); off by default — see [`docs/mcp.md`](./mcp.md) |
| `/v1/lab/*` | Teaching scenarios + Academy curriculum/progress/sandbox/missions/prompts/cohorts/workbench/capacity/instructor |
| `/v1/help/glossary` | Authored help glossary from `config/help/glossary.yaml` |
| Worker `/v1/generation/*` | Stub `not_supported` until runtime selected |
| Worker `/v1/speech/*` | Whisper-class STT + Piper TTS (or dry_run mock); never invent READY |

### Voice (STT/TTS)

Browser captures mic audio and plays replies; **all** speech inference goes through
Gateway → worker speech runtime. Chat tokens still use `/v1/chat/completions`.
Until Whisper/Piper (or an explicit dry_run mock) is loaded, speech APIs report
`not_supported` / `not_configured` / `not_reported` — the console must not pretend
to be listening. Voice **commands** require a leading wake word in the transcript
(`Yonder …`); ordinary speech is dictation only.

## Request flow (Phase 2)

```text
Receive request
      ↓
Assign Yonder request ID
      ↓
Resolve trusted request context
      ↓
Validate OpenAI-compatible request
      ↓
Resolve model alias
      ↓
Check route and deployment readiness
      ↓
Apply admission controls
      ↓
Dispatch through InferenceProvider adapter
      ↓
Stream or return normalized response
      ↓
Record operational telemetry
```

API routes depend on `ChatService` + `ProviderRegistry`. They must not import concrete
providers such as `MockStreamingProvider`.

Every provider adapter must pass the reusable conformance checks documented in
[`provider-conformance.md`](provider-conformance.md). Token usage is returned only
when a provider supplies authoritative tokenizer counts; otherwise `usage` is null.

### Chat context window (auto-condense)

When `context_window.enabled` is true (lab default), `ChatService` measures soft
pressure before admission:

* Denominator = deployment `runtime_configuration.context_length` (load-time
  `n_ctx`). If missing, meter is `not_reported` and condensation is skipped.
* Soft pressure uses a labeled char÷N **estimate** (`chars_per_token_estimate`).
  That estimate is never exported as `prompt_tokens`.
* When estimate fill crosses `soft_ratio`, Gateway summarizes older turns via the
  same provider (with `yonder_skip_condense` to prevent recursion), persists the
  condensed transcript when `conversation_id` is set, and emits an SSE
  `yonder.context` event so Chat UI syncs. Hard `max_prompt_chars` / runtime
  `n_ctx` refusals remain fail-closed.

## Local Mac development

Until the GPU server is available:

* Run gateway and worker on `127.0.0.1`.
* Expect `gpu_discovery_status: not_supported` because `nvidia-smi` is absent.
* Host CPU/memory/storage/network metrics still come from the real machine.
* Chat uses the gateway `llama-cpp` provider behind alias `yonder-chat` on lab
  hosts with the runtime enabled. Mac-local defaults still use the labeled
  `mock` provider when `llama_cpp.enabled` is false.
* GPU placement (single-GPU or two-GPU pool) is gateway-mediated; see
  [`gpu-model-scheduler.md`](gpu-model-scheduler.md). Soft multi-lane sharing
  allows up to `capacity.max_lanes_per_gpu` (default 3) occupying deployments
  per physical GPU when measured free VRAM still fits. EnsureReady may
  auto-load and idle-evict under policy (locks and the sole chat lane are
  protected; VRAM admission stays fail-closed).
* Training Lab + Academy (`/v1/lab/*`); see [`lab-training.md`](lab-training.md) and [`academy.md`](academy.md).
* Do not invent a fake V100 inventory for demos.
