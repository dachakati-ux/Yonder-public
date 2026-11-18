# Internal local coder — phases, trust ceiling, proof gates.

Yonder Coder is a **Gateway-only**, **locally hosted** coding track.

Trust ceiling defaults to **read-only**. Write/exec/push unlock is explicit
(`coder.write_unlock`) and stays **false** until proof gates pass.

Phases A–G below are shipped plumbing. The road to long-running autonomy
(phases H–L) is planned in
[coder-autonomy-plan.md](coder-autonomy-plan.md).

## Phases

| Phase | Product | Tools | Gate to next |
| --- | --- | --- | --- |
| **A** | Secure code assistant on alias `yonder-coder` | None (paste / propose diffs) | Operator proof pack + honest readiness |
| **B** | Sandboxed read agent | `list_dir` / `read_file` / `search` on a cage | Jail escape tests + audited dogfood |
| **C** | Human-directed path | Same tools on a **granted** root under policy | Same jail; human picks path |
| **D** | Local git intake | `POST /v1/coder/workspaces/clone` into cage | Grantable-root clone + durable metadata |
| **E** | Model / deterministic tool loop | Workspace-scoped ToolExecutor | Agent turns audited; max turns |
| **F** | Write unlock | `apply_patch` + allowlisted `run_check` | Escape/secret/allowlist tests green |
| **G** | Staged pipeline + dual-agent push | Runs: intake→…→dual_verify→push | **Two approve verdicts** before local push |

```text
Console /coder ──► Gateway /v1/coder/*
                 ├─► workspaces / tools / runs
                 └─► Chat desk=coder ──► /v1/chat/completions (yonder-coder)
```

Forgejo clone/PR is stubbed (`ForgejoAdapter`) and **not** enabled. Local push
targets a bare remote under the cage metadata tree.

## Pipeline stages

| Stage | Allowed | Exit |
| --- | --- | --- |
| `intake` | Record base head | Workspace id + base recorded |
| `explore` | Read tools + notes | Explore summary artifact |
| `plan` | Read tools + notes | Numbered plan artifact |
| `implement` | `write_file` / `apply_patch` / `run_check` if unlocked | Real changes on disk (checkpointed) |
| `check` | Allowlisted command | Real exit code stored; fail → fix loop |
| `dual_verify` | Verifiers A + B, evidence-only | Both `approve`, or fix loop / fail |
| `push` | Local branch + push | SHA recorded |

**There is no deterministic/pretend agent mode.** Every agent stage calls the
locally hosted model (`coder.model_alias`, default `yonder-coder`) through the
gateway or the run fails with `llm_unavailable`. Failed checks feed their
stdout/stderr back into the next implement iteration
(`coder.max_fix_iterations`). Verdicts fail closed: missing or non-passing
check evidence rejects — it never approves. Push requires two approve
verdicts **and** passing check evidence.

Runs execute in a background executor (per-run lock, budget enforcement,
cancellation via `POST /v1/coder/runs/{id}/cancel`) and are resumed by a boot
sweep after a gateway restart.

### Docs search (gateway-native, read-only)

The coder agent has a `coder_docs_search(query, k)` tool that runs RAG over a
docs corpus so it can look up real APIs and Yonder behavior instead of
guessing. This is **gateway-native, not MCP** — the docs live inside Yonder's
trust boundary, so there's no external server. The corpus (a separate
`docs.sqlite3` RAG store) is seeded at startup with Yonder's own docs
(`docs/*.md`, `README.md`, `AGENTS.md`) plus a repo-sourced dependency
inventory; vendor real third-party library docs into `config/lab/docs-corpus/`
and run `scripts/ingest-docs.py` to add them. Gated by `coder.docs_search_enabled`
(default on); results are secret-scanned. Phase 1 is lexical retrieval (offline,
no model); Phase 2 hybrid retrieval (lexical + embedding cosine, optional
rerank) is opt-in via `coder.docs_embeddings_enabled` with `docs_embed_alias`
and falls back to lexical if the embed backend isn't ready — it never
fabricates vectors. See
[`config/lab/docs-corpus/README.md`](../config/lab/docs-corpus/README.md).

### MCP tools for the coder (gated, per-run)

The coder agent can call external MCP tools, but only ones explicitly named in
`coder.mcp_tools` (qualified `mcp.<server>.<tool>`). Default is empty — no MCP
tools (fail closed). These are registered on the **per-run** tool executor with
the run's principal and id, so every call is attributed and audited through the
gateway MCP client (which already secret-scans output and enforces the URL
allowlist). This deliberately does **not** use the global
`mcp.bridge_into_tool_executor` bridge, which stays off. Requires `mcp.enabled`
and the relevant server configured; see [`docs/mcp.md`](mcp.md). Budgets: `max_model_calls`,
`max_wall_clock_seconds`, `max_fix_iterations`, `context_budget_tokens`
(estimated-token prompt assembly with conversation eviction; durable
findings live in the run's notes artifact via `coder_update_notes`).

## Trust ceiling (non-negotiable now)

* No free-form shell; only allowlisted check commands.
* Path traversal and absolute paths fail closed.
* Secret-like names (`.env`, `*.pem`, `id_rsa`, …) are denied for read **and** write.
* Every workspace create/grant/clone/revoke, tool call, stage transition, verdict,
  and push is audited via the event store.
* Capability: `coder.use` (or `lab.use` for lab practice).
* Network remotes (https://…) rejected until Forgejo is configured.

`write_unlock` in config defaults to **false**. Do not flip it in lab until the
safety checklist below is green.

## Console

* `/coder` — desk: lane status, cage/grant/clone, pipeline runs + timeline,
  dual verdicts, read-tool proof UI.
* Chat binder: `/?desk=coder&model=yonder-coder&system=…` locks the alias and
  applies the reviewed system contract. Trust chips: `read_only` / `write_locked`,
  workspace presence, alias `ready|not_ready|not_reported`.

## Gateway APIs

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/coder/status` | Phase, trust ceiling, write_unlock, pipeline |
| GET | `/v1/coder/workspaces` | Principal-scoped grants |
| POST | `/v1/coder/workspaces` | Create cage under `coder.cage_root` |
| POST | `/v1/coder/workspaces/grant` | Grant path under grantable roots |
| POST | `/v1/coder/workspaces/clone` | Local clone/import into cage |
| DELETE | `/v1/coder/workspaces/{id}` | Revoke (deletes cage/clone files) |
| POST | `/v1/coder/tools/list_dir` | Jailed list |
| POST | `/v1/coder/tools/read_file` | Jailed read (size-capped) |
| POST | `/v1/coder/tools/search` | Jailed substring search |
| POST | `/v1/coder/tools/apply_patch` | Write unlocked only |
| POST | `/v1/coder/tools/run_check` | Allowlisted check, write unlocked only |
| GET/POST | `/v1/coder/runs` | List / create pipeline runs |
| GET | `/v1/coder/runs/{id}` | Run detail |
| POST | `/v1/coder/runs/{id}/advance` | Submit to background executor (`{wait:true}` blocks) |
| POST | `/v1/coder/runs/{id}/cancel` | Cancel between steps (terminal `cancelled`) |

Grantable roots = config `coder.grantable_roots` plus cage root, plus
`config/lab/coder-evals` when present (proof fixtures).

## Model lane

* Manifest: `config/models/yonder-qwen3-coder-next-q4.yaml` (two-GPU on V100).
* Alias: `yonder-coder` — bind after a successful load.
* Load canary: `scripts/canary-coder-two-gpu.sh`
* Never invent READY; Capacity / Models report `not_ready` / `not_reported`.

## Proof pack

* Cases: [`config/lab/coder-evals/phase-a-cases.yaml`](../config/lab/coder-evals/phase-a-cases.yaml)
* Sample grant/clone tree: `config/lab/coder-evals/sample_repo/` (+ `test_math_utils.py`)
* Tool/jail canary: `scripts/canary-coder-assistant.sh`
* Pipeline canary: `scripts/canary-coder-pipeline.sh`
* Record **real** outcomes in `WORKLOG.md`. Do not invent VIP scores.

### Safety checklist before `write_unlock=true` in lab

1. Path-jail + secret denial tests green
2. Apply cannot escape cage or write secrets
3. `run_check` rejects non-allowlisted commands
4. Push refused without two `approve` verdicts
5. Operator canary: clone sample_repo → auto pipeline → local branch SHA
6. Only then flip `coder.write_unlock`

### VIP demo script (~10 minutes, Phase A)

1. `/coder` — show trust chips + alias availability (honest if not ready).
2. If not ready: Models / two-GPU canary; do not fake green.
3. Open coder chat — paste `samples/bug.py` style bug; ask for PROPOSED ONLY diff.
4. Show overclaim chip if the model claims it “ran” or “applied” something.
5. (Optional) Create cage → List / Read / Search; show 403 on `../`.
6. (Optional pipeline, write locked) Start run → advance until blocked at implement.
7. Close: local, Gateway-only, push gated by dual agents when unlocked.

## What this is not

* Not Cursor / cloud agents.
* Not free-form shell on lab hosts.
* Not Forgejo PR automation yet (adapter stub only).
* Not company-wide IAM (air-gap principals via development / trusted ingress later).

See also [operations.md](./operations.md) (coder canary), [academy.md](./academy.md),
[security-boundary.md](./security-boundary.md).
