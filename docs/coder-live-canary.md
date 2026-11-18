# Coder live canary + transfer runbook

Everything the autonomous coder needs is built and unit-tested on Mac-local.
The one thing that can only happen on a GPU host is proving the agent loop
against a real model. This runbook is the path from "transferred to the
server" to "a seeded bug fixed autonomously, reviewed by a human."

There are two ways to drive a **real** model before ai1 is ready, and one on
ai1 itself. None of them mock anything — a run only completes if the model
produced a passing change and both verdicts approved.

## Option A — LM Studio on the Mac (fastest local proof)

Use this to shake out tool-calling and the loop before touching the lab.

1. In LM Studio → Developer tab: start the server, enable **Serve on Local
   Network**, port **1234**, and load a coder model (a Qwen coder GGUF best
   matches ai1).
2. Point the gateway at it via the external-attach path (no GPU worker
   needed). Set in `.env` or environment before `make gateway`:

   ```bash
   YONDER_LLAMA_CPP__ENABLED=true
   YONDER_LLAMA_CPP__BASE_URL=http://<mac-lan-ip>:1234
   YONDER_LLAMA_CPP__SERVED_MODEL=<model id from /v1/models>
   YONDER_LLAMA_CPP__ATTACH_ALIASES=yonder-coder
   YONDER_LLAMA_CPP__ATTACH_CONTEXT_LENGTH=8192
   YONDER_CODER__WRITE_UNLOCK=true          # canary only, isolated machine
   YONDER_CODER__MODEL_ALIAS=yonder-coder
   ```

   The gateway registers a READY `yonder-coder` deployment and, at startup,
   calls the provider's `load()` which verifies `SERVED_MODEL` against the
   endpoint's `/v1/models` — so a wrong model id or a down server fails
   honestly instead of pretending.
3. Run one task:

   ```bash
   GATEWAY_URL=http://127.0.0.1:8080 scripts/canary-coder-live.sh bug-subtract-add
   ```
4. Then the full suite:

   ```bash
   python3 scripts/coder-eval.py --gateway http://127.0.0.1:8080 \
       --json data/eval-lmstudio.json
   ```

## Option B — ai1 with the real coder lane

1. Transfer the repo (or pull the branch) to ai1. Bring up the stack per
   `docs/multi-host-deployment.md` with the worker shared secret set on both
   control plane and worker (see `docs/worker-registration.md`).
2. Load the coder model and enable the alias:

   ```bash
   # via console Models workspace, or the lifecycle API:
   scripts/lab-set-stable-aliases.sh    # binds yonder-coder after a placed load
   ```
   Confirm `yonder-coder` is READY: `curl $GATEWAY/v1/models`.
3. **Before** enabling write, measure real context headroom (8K is the
   binding constraint — do not guess it):

   ```bash
   MODEL=/srv/yonder-storage/model-bundles/Qwen3-Coder-Next-GGUF/*UD-Q4_K_XL.gguf \
   LLAMA_SERVER=/opt/llama.cpp/llama-server \
   scripts/measure-coder-context.sh
   ```
   Record the largest context that stays within 32 GB/GPU into
   `config/models/yonder-qwen3-coder-next-q4.yaml`
   (`placement_limits.two_gpu.max_context_length`, `evidence: measured`) and
   set `YONDER_CODER__CONTEXT_BUDGET_TOKENS` to ~70% of it.
4. Enable write **only** with check isolation configured (see safety gate
   below), then run the canary and eval suite exactly as in Option A but with
   `GATEWAY_URL` pointing at ai1 and `CODER_SUBJECT` set if the gateway runs
   in `trusted_ingress` mode.

## Safety gate before `write_unlock=true` on a shared host

`run_check` executes the target repo's own test suite. On ai1 that must not
run as the gateway user. Set:

```bash
YONDER_CODER__CHECK_ISOLATION=podman     # or systemd-run for limits-only
YONDER_CODER__CHECK_ISOLATION_IMAGE=registry.access.redhat.com/ubi9/python-312
```

If the isolation binary is missing, checks fail closed
(`isolation_unavailable`) — they never silently run unisolated. Verify podman
is installed on ai1 first: `podman --version`. The phase-J test pack
(`apps/gateway/tests/test_coder_security.py`) proves jail escapes, secret
redaction, and injection-as-data hold; keep it green.

## First live results (2026-08-06, LM Studio `prism-ml/bonsai-27b`)

The full loop was proven end-to-end against a real model via the Option A
attach path (gateway → LM Studio on the LAN), no mocks:

| Task | Kind | Result | Model calls | Fix iters | Wall clock |
| --- | --- | --- | --- | --- | --- |
| bug-subtract-add | bugfix | ✅ completed, dual-approved, pushed | 10 | 0 | 102 s |
| feature-multiply | feature | ✅ completed, dual-approved, pushed | 10 | 0 | 126 s |

Both runs went intake→explore→plan→implement→check→dual_verify→push with the
real `pytest` returning exit 0 and both verifiers approving on evidence.
Observed tuning that mattered (bonsai is a reasoning-heavy model):

- `admission.inference_timeout_seconds` had to rise to ~300 (a chatty model
  can take >120 s for one turn). Set `YONDER_ADMISSION__INFERENCE_TIMEOUT_SECONDS`.
- `max_output_tokens` ~1200 kept each turn from over-reasoning; too high made
  single calls slow, too low truncated tool calls.
- `max_agent_turns` 4 was enough per stage for these tasks; raise for harder
  ones once context headroom is measured (K3).

Qwen coder models on ai1 should need less of the reasoning headroom bonsai
wanted; re-tune with the eval harness there.

## What "passing" looks like

For each eval task the harness reports `status=completed`,
`dual_approved=true`, the number of model calls, fix iterations, and
wall-clock. A completed run means: the model edited files, the real check
command passed, two verdicts approved on evidence, and a branch was pushed to
the local bare remote. Target for phase-I exit: ≥7/10 (here 8) tasks green.

## Coder tools: Serena, docs search, Forgejo delivery

Config to activate the coder-agent tools built in the roadmap. All fail closed.

### Serena (code intelligence) — run as streamable-HTTP, not stdio

```yaml
mcp:
  enabled: true
  http_url_allowlist: ["http://127.0.0.1:8900"]
  servers:
    - name: serena
      transport: http
      url: http://127.0.0.1:8900/mcp
      enabled: true
coder:
  mcp_tools:
    - mcp.serena.get_symbols_overview
    - mcp.serena.find_symbol
    - mcp.serena.find_referencing_symbols
```
Run Serena on the coder host: `uvx --from serena-agent serena start-mcp-server
--transport streamable-http --host 127.0.0.1 --port 8900 --project <repo>
--context ide-assistant`. Verify with `GET /v1/mcp/tools` before a run.
(Proven live end-to-end: our client discovered 21 tools and returned real
symbols; the per-run allowlist withheld everything not listed.)

### Docs search — Phase 1 (lexical, default) and Phase 2 (embeddings)

Phase 1 works offline with no model. To enable Phase 2 hybrid retrieval once an
embedding alias is READY:

```yaml
coder:
  docs_search_enabled: true       # default
  docs_embeddings_enabled: true   # opt-in; needs a READY embed alias
  docs_embed_alias: yonder-embed
  docs_hybrid_alpha: 0.5          # 0=lexical only, 1=embedding only
```
Vendor real library docs into `config/lab/docs-corpus/` and run
`python scripts/ingest-docs.py config/lab/docs-corpus`. If the embed backend
isn't ready, retrieval silently stays lexical — it never fabricates vectors.


### Forgejo delivery (Phase L) — opens a PR after the local push

```yaml
coder:
  forgejo_base_url: http://127.0.0.1:3001
  forgejo_token_ref: YONDER_FORGEJO_TOKEN   # env var NAME, not the token
  forgejo_owner: labadmin
  forgejo_base_branch: main
```
Set the token in the environment (`YONDER_FORGEJO_TOKEN=...`). Delivery is
additive and fail-soft: the local branch push always happens; a Forgejo failure
is recorded in `push_result.forgejo` but never fails an otherwise-complete run.
Validate live on ai1 (needs the running Forgejo from
`scripts/provision-ai1-forgejo.sh`).

## Tuning knobs (all in `coder:` config)

| Setting | Purpose |
| --- | --- |
| `model_alias` | Which alias the agent calls (default `yonder-coder`) |
| `verifier_model_alias` | Optional 2nd model for verifier B independence |
| `max_agent_turns` | Tool-loop turns per stage |
| `max_fix_iterations` | Check→fix retries before failing |
| `max_model_calls` / `max_wall_clock_seconds` | Run budgets |
| `context_budget_tokens` | Prompt-assembly budget (set from measured ctx) |
| `max_output_tokens` | Per-call generation cap |
| `check_isolation` | `none` / `systemd-run` / `podman` |

## If tool-calling is rough

Small local models vary in tool-call fidelity through the llama.cpp chat
template. If the agent narrates instead of calling tools, or emits malformed
tool JSON, the fix is at the provider/template layer, not the loop:
- confirm the model was started with a tool-capable chat template,
- try `tool_choice` behavior and a lower temperature,
- consider a coder model with stronger native tool support.
The loop already records `is_error` on tool results and feeds check failures
back — capability, not plumbing, is what the canary measures.
