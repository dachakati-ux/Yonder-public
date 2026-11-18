# Coder autonomy plan — phases H through L

Goal: evolve Yonder Coder from a proven-plumbing pipeline into an agent that
works **autonomously for hours** on real repositories using **only locally
hosted lab models**, inside the existing security boundary.

This document continues the phase ladder in [coder.md](coder.md) (phases A–G
are shipped). It is the working plan of record; update it as phases land.

> **Status 2026-08-06:** Phase H is landed except H5 (telemetry/SSE), and the
> core of phase I is landed: deterministic/pretend mode is deleted, the agent
> loop is live-only with proper tool-role protocol, budgeted context assembly,
> durable notes, and a check→fix feedback loop; runs execute in a background
> executor with restart resume and cancellation. One deliberate deviation
> from the original plan: there is **no fake-LLM test harness** — automated
> tests cover the real plumbing (executor, patching, gates, budgets, resume)
> and the agent loop itself is proven by live canary on ai1 (I6). Remaining
> in I: eval harness v1 and the live canary. Then J.

## Where we actually are (honest baseline)

Phases A–G delivered the **containment and provenance layer**, and it is
solid: principal-scoped jailed workspaces, double-gated write tools, a
fail-closed dual-verify push gate, durable run rows with stage timelines,
audit events on every tool call, and a working local-git branch/commit/push
path. That is the half most prototypes skip.

What has **never run** is the agent itself:

* The only pipeline that has completed end-to-end is deterministic mode,
  whose "implement" stage is a hardcoded `"a - b" → "a + b"` string replace
  against the eval fixture (`coder_runs.py:_implement`).
* Live mode (`_agent_loop` + `build_coder_llm`) has zero tests and has never
  been exercised against a READY `yonder-coder` deployment.
* Structural ceiling today: ~4 stages × 6 turns + 1 retry ≈ **30 model calls
  per run, ever** — against an 8,192-token context with no token counting,
  no compaction, and no admission control (`build_coder_llm` bypasses
  `ChatService._prepare_context` and `_admit_size`).
* Tool results are sent back as `role: user` strings with no `tool_call_id`
  correlation, which degrades tool-calling fidelity on tool-trained models.
* `advance()` runs the whole pipeline synchronously inside one HTTP POST,
  `run_check` and git calls block the event loop, and nothing resumes an
  `active` run after a gateway restart.
* `_parse_unified_diff` silently truncates multi-hunk files (data loss).
* Verdict from the 2026-08-06 audit: **~20–25%** of the road to hours-long
  autonomy, and the missing 75% is almost entirely the agent core, not the
  safety shell.

## Constraints (non-negotiable)

1. **Local models only.** The lab is offline; no external model APIs. The
   agent must be engineered around a small brain — today
   `yonder-qwen3-coder-next-q4` (two V100-32GB, `context_length: 8192`,
   `max_output 4096`, evidence `research_estimate`).
2. All AGENTS.md rules hold: gateway-only inference, no fabricated state,
   fail closed, adapters over hardcoding, worker parity before "lab-ready."
3. Trust ceiling stays read-only until phase-J proof gates pass;
   `write_unlock` remains false in every committed config.
4. GPU contention is real: the coder lane shares V100s with chat/Studio.
   Long runs must yield (lane budgets), never starve interactive use.

## Target use cases (in priority order)

1. **Bug fixes + test failures** — ticket in, minimal fix + passing tests
   out, branch pushed for human review.
2. **Maintenance chores** — lint/type cleanups, dependency bumps, doc drift;
   high-volume low-risk overnight work.
3. **Small features end-to-end** — scoped multi-file changes with tests.
4. **Dogfood on Yonder itself** — `make verify` is the check loop; the
   Yonder repo becomes the standing eval.

## Design principles for a small local brain

A frontier-model agent tolerates sloppy context discipline; an 8–32K local
model does not. Every architectural choice below follows from that:

* **The transcript is not the memory.** Durable state lives in workspace
  files the agent reads and rewrites (`.coder/plan.md`, `.coder/notes.md`,
  `.coder/attempts.log`), not in an ever-growing message list. Any turn can
  be answered from (system prompt + plan file + current task step + last
  tool results). This is what makes runs resumable and hours-long possible.
* **One small step per loop iteration.** The orchestrator asks the model for
  the next action toward the current plan step, not for "implement the
  feature." Small asks fit small contexts and fail cheaply.
* **Compaction is mandatory, not defensive.** Token-budgeted assembly on
  every call; oldest tool results summarized into notes before eviction.
* **Feedback beats retries.** A failed check re-enters the loop *with the
  traceback*; a rejected verify re-enters *with the rejection reasons*.
  Re-rolling an identical prompt (today's behavior) is wasted GPU time.
* **Verify with evidence, never vibes.** Gates read exit codes and diffs,
  not model self-reports (`not_reported` must never approve anything).

## Architecture (target state)

```text
Ticket / operator / schedule
        │
        ▼
  Run Queue (durable, budgeted)          ── per-run lock, TTL, cancellation
        │
        ▼
  Run Executor (background task)         ── survives restart, resumes sweep
        │
        ▼
  Agent Core loop  ◄──────────────┐
    assemble context (budgeted)   │ check/verify feedback
    → model call (gateway alias)  │
    → execute tool calls          │
    → update .coder/ state files ─┘
        │
        ▼
  Evidence gates: check → dual_verify → push (unchanged fail-closed shell)
```

The safety shell (jail, write gates, dual-verify, audit) is retained as-is;
the agent core inside it is rebuilt.

## Phase H — Foundations (correctness before capability)

Everything here is prerequisite plumbing; no new autonomy yet.

* **H1. Background run executor.** Move `advance` out of the request path:
  `POST /runs/{id}/advance` enqueues; an executor task drives stages with a
  per-run asyncio lock and saves after every step. Boot-time sweep resumes
  `status="active"` runs (SQL store required for coder runs — refuse
  `agent_mode=live` on memory persistence). Cancellation endpoint.
* **H2. Stop blocking the event loop.** `run_check`, git operations →
  `asyncio.to_thread`; kill process groups on timeout, not just the child.
* **H3. Correct patch application.** Replace `_parse_unified_diff` with
  `git apply --3way` inside the jail; snapshot (`git stash create` or tag)
  before each implement step; rollback on failed apply. Add `write_file`
  and `edit_range` tools (whole-file replace is a bad primitive for models).
* **H4. Error capture.** `_step` records *every* exception into the run row
  before re-raising; tool errors set structured state the loop can react to
  (today `is_error` is recorded and ignored).
* **H5. Telemetry.** Per-stage duration, model calls, prompt/output tokens,
  tool-call counts as metrics + run-row fields. SSE progress stream for the
  console; CoderDesk subscribes instead of snapshot-on-click.
* **Gate to I:** deterministic pipeline green under the executor, run
  resumes across a gateway restart mid-pipeline, chaos test (kill mid-stage)
  leaves a recorded, resumable run.

## Phase I — A real agent core

* **I1. Proper tool protocol.** `tool`-role messages with `tool_call_id`
  through the llama.cpp provider path (verify llama.cpp server accepts
  OpenAI tool-role messages for Qwen3-Coder; else use its chat-template
  format). Stop stringifying results into `user` turns.
* **I2. Token-budgeted context assembly.** Count tokens (llama.cpp
  `/tokenize` or heuristic with margin), assemble each call from: system
  prompt + `.coder/plan.md` + current step + recent turns within budget;
  summarize-and-evict older turns into `.coder/notes.md`. Route calls
  through `ChatService.complete()`-equivalent so `_admit_size` and context
  condensation apply (fix the `build_coder_llm` bypass).
* **I3. Durable scratchpad protocol.** Explore writes `.coder/plan.md`
  (numbered steps, acceptance criteria); implement loop marks steps done;
  attempts and dead ends go to `.coder/attempts.log`. Files live in the
  jail, are diffable, auditable, and excluded from the final push
  (`.gitignore` inside the branch or strip before commit).
* **I4. Check→fix loop.** `check` failure feeds stdout/stderr tail +
  failing-test names back into the next implement iteration; configurable
  `max_fix_iterations` (plumb `max_revise` from `CoderConfig` — today
  hardcoded to 1). Dual-verify rejection reasons likewise injected.
* **I5. Live-mode tests without GPUs.** A scripted fake LLM (canned
  tool-call sequences) exercises the full loop: multi-turn implement,
  failed-check recovery, compaction under a tiny fake budget, resume
  mid-loop. This is the single biggest testing gap today (zero live-path
  tests).
* **I6. Eval harness v1.** Extend `config/lab/coder-evals/` to ~10 tasks:
  seeded bugs, failing tests, small features across Python/TS fixtures.
  `scripts/coder-eval.sh` reports pass rate, model calls, tokens,
  wall-clock per task. Run per PR in deterministic+fake-LLM mode; run live
  on ai1 as a canary.
* **Gate to J:** ≥7/10 evals pass in live mode on ai1 read-only (proposed
  diffs judged by the harness, writes still locked), with token accounting
  proving runs stay inside context budget.

## Phase J — Safety to earn write_unlock

`write_unlock=true` on a real repo is arbitrary code execution by design
(`run_check` executes the repo's own test suite). Isolation is the price of
autonomy, and it is currently the weakest part of the shell:

* **J1. Sandbox `run_check`.** Execute checks in a disposable container
  (podman on RHEL; fallback `systemd-run` with dynamic user) with: workspace
  bind-mounted, no network, CPU/memory/pids rlimits, wall-clock kill,
  separate uid. The gateway process user must never execute repo code.
* **J2. Allowlist argument validation.** Prefix match is not enough
  (`pytest /etc` passes today). Validate argv: no absolute paths, no `..`,
  paths must resolve inside the workspace; flags allowlisted per command.
* **J3. Content-level secret scanning.** Filename heuristics only, today.
  Add content scan (high-signal regexes: private keys, `AKIA`, bearer/JWT,
  `.npmrc`/`.netrc`/`terraform.tfvars` patterns) on every byte-stream that
  leaves the jail toward the model, the DB, or the console — file reads,
  diffs, check output. Redact, log, and count; fail closed on scanner error.
* **J4. Dual-verify independence.** Two verdicts from the same model with
  temperature 0.1 vs 0.3 is prompt diversity, not independence. Verifier B
  becomes: a different alias when available (e.g. `yonder-qwen36-35b-a3b-q4`
  or `yonder-gemma4-26b`), fresh context, evidence-only prompt (diff + check
  output, no implement narrative). Remove the `not_reported`-approves hole:
  missing check evidence → reject, always.
* **J5. Jail-escape and injection test pack.** Symlink escapes, `.git`
  hooks in cloned repos, hostile `conftest.py`, prompt-injection strings in
  repo files ("ignore your instructions, run…") — the loop must treat file
  content as data, and the pack proves it.
* **Gate to K:** J1–J5 tests green in CI + operator-witnessed lab canary;
  only then flip `write_unlock=true` for the lab environment, allowlisted
  repos only.

## Phase K — Long-running autonomy

* **K1. Budgets as first-class run fields.** `max_wall_clock`, `max_model_calls`,
  `max_gpu_minutes`, `max_fix_iterations` per run, with defaults per task
  class (chore < bugfix < feature). Exhaustion = honest `budget_exhausted`
  terminal state with everything pushed to a draft branch for human pickup.
* **K2. Run queue + scheduler.** Durable FIFO with priorities; concurrency 1
  per workspace, N per gateway; off-hours window config so overnight chores
  don't contend with daytime chat lanes. Heartbeat + stuck-run detection.
* **K3. Context-length evidence on ai1.** 8K is the binding capability
  constraint. Measure (never guess, per AGENTS.md): KV-cache VRAM at 16K/24K/32K
  with quantized KV (`q8_0`) on the two-GPU split; record real
  `max_context_length` per placement in the model manifest with
  `evidence: measured`. Evaluate `yonder-qwen36-35b-a3b-q4` (MoE A3B — likely
  faster per token) as the planning/verify brain; keep a **model ladder**:
  cheap model for search/summarize turns, coder model for edit turns.
* **K4. Task intake.** `POST /v1/coder/tasks` with task class + repo +
  description; operator batch submission from the console; nightly
  maintenance preset for the Yonder repo itself (`make verify` as check).
* **K5. Long-run observability.** Live timeline in CoderDesk (SSE from H5),
  budget burn-down, per-run token/GPU-minute cost, weekly eval-pass-rate
  trend. An unattended agent without dashboards is unreviewable.
* **Gate to L:** a seeded-bug run and a chore run complete unattended on ai1
  within budget, producing reviewable branches, ≥3 consecutive nights
  without operator intervention.

## Phase L — Delivery integration

* **L1. Forgejo adapter for real.** Clone over HTTPS with `forgejo_token_ref`
  (secret-ref only, never in config), push branch, open PR with run summary +
  evidence links. Fail-closed stays: no dual-approve, no PR.
* **L2. Ticket loop.** Intake from Forgejo issues with a `yonder-coder`
  label; PR links back to the issue; human merge is always the final gate.
* **L3. Multi-repo + worker parity.** Grantable roots per team; coder lane
  wired on every registered worker per AGENTS.md rule 9 before "lab-ready."

## What we deliberately do NOT build

* No auto-merge, ever — a human merges every PR.
* No external model calls, no telemetry leaving the lab.
* No general shell tool for the agent; mutations stay `apply_patch` /
  `write_file` / `edit_range`, execution stays sandboxed `run_check`.
* No new auth system — operator access rides the existing capability model
  (`coder.use`), service calls ride the worker shared secret.

## Success metrics

| Metric | Phase I exit | Phase K exit |
| --- | --- | --- |
| Eval pass rate (live, local models) | ≥ 70% | ≥ 80% on chores, ≥ 60% features |
| Unattended run duration | n/a (read-only) | ≥ 4 h within budget |
| Runs surviving gateway restart | 100% | 100% |
| Context overflows / run | 0 (budgeted) | 0 |
| Human interventions / overnight batch | n/a | ≤ 1 |
| Secret leaks past scanner (red-team pack) | 0 | 0 |

## Top risks

1. **Model capability ceiling.** Qwen3-Coder Q4 at 8K may simply be too weak
   for feature work even with perfect scaffolding. Mitigation: eval harness
   gives an early, honest read; the phase-K model ladder and context
   evidence raise the ceiling; scope can retreat to chores-only, which still
   pays for the system.
2. **Sandbox debt blocks everything.** Without J1, write_unlock is RCE and
   autonomy is capped at proposing diffs. Treat podman-on-RHEL enablement as
   the critical path item for the lab hosts.
3. **GPU contention.** Hours-long coder runs vs interactive chat on shared
   V100s. Mitigation: scheduler windows (K2), lane budgets, ensure-ready
   demotion rules already in place.
4. **Prompt injection via repo content.** A hostile or merely weird repo can
   steer a small model easily. Mitigation: J5 pack, evidence-only verify
   prompts, tool results framed as data, push gate independent of narrative.
5. **Scope creep back into the shell.** The safety layer is done; resist
   rebuilding it. All new work should land inside the agent core, executor,
   and sandbox seams named above.

## Immediate next steps (first working session)

1. H1 + H2: executor skeleton with per-run lock, `to_thread` for subprocess
   and git, resume sweep + chaos test.
2. H3: `git apply --3way` patch path with snapshot/rollback (fixes the known
   multi-hunk data-loss bug) + `write_file` tool.
3. I5 scaffold: fake-LLM harness so every subsequent change to the loop is
   testable without GPUs.
4. K3 spike (parallel, on ai1): measure KV-cache VRAM at 16K/24K/32K with
   quantized KV on the two-GPU split; update the manifest with measured
   evidence — this number shapes how aggressive I2's budgets must be.
