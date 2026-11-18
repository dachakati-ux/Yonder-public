# Yonder Academy

Interactive Training Lab: ordered curriculum, principal-scoped progress,
Gateway-mediated practice sandboxes, workflow missions, cohorts/challenges,
workbench notes, capacity literacy, and a governed prompt library on top of
Lab / Recipes / Help.

## Surfaces

* Console: `/academy`, `/academy/lessons/[id]`, `/academy/sandbox`,
  `/academy/missions`, `/academy/missions/[id]`, `/academy/prompts`,
  `/academy/cohorts`, `/academy/workbench`, `/academy/capacity`,
  `/academy/instructor`
* Gateway: `GET /v1/lab/curriculum`, progress APIs, `/v1/lab/sandbox/*`,
  `GET /v1/lab/missions`, `POST /v1/lab/missions/{id}/events`,
  `GET /v1/lab/prompts`, `/v1/lab/cohorts*`, `/v1/lab/workbench*`,
  `GET /v1/lab/capacity`, `/v1/lab/instructor/*`
* Content: [`config/lab/curriculum.yaml`](../config/lab/curriculum.yaml),
  [`config/lab/lessons/`](../config/lab/lessons/),
  [`config/lab/missions/`](../config/lab/missions/),
  [`config/lab/cohorts.yaml`](../config/lab/cohorts.yaml),
  [`config/lab/prompts.yaml`](../config/lab/prompts.yaml),
  corpora `corpus.yaml` / `corpus-site-ops.yaml` / `corpus-support.yaml` /
  `corpus-ops.yaml` / `corpus-eng.yaml`
* Capabilities: `lab.use` for learners; `academy.instruct` (or
  `operations.manage`) for instructor console

## Auth-ready progress (no Yonder accounts)

Progress, sandboxes, missions, challenges, and workbench notes are keyed only
by `RequestPrincipal.subject` from the replaceable `RequestContextProvider`.
Yonder does **not** create passwords, registration, or local employee databases.

| Today (lab — pending corporate auth review) | After corporate auth cutover |
| --- | --- |
| `security.mode=development` + lab mock principal | `trusted_ingress` (or approved provider) only |
| Shared lab operator subject for demos | Real employee/service subjects from upstream |
| Same progress/sandbox/mission/cohort APIs | New subjects appear; no schema rewrite |
| Capabilities from env config (`lab.use`, `academy.instruct`) | Same checks; upstream decides who gets them |

### Corporate auth cutover — deferred

Corporate auth cutover (trusted ingress, revoke the lab mock principal, firewall
hardening) is **on hold** while Tier 2 scale-adoption features ship. Principal-
scoped APIs stay ready; do not build login pages or upstream IAM guesses now.
This lab posture is **explicitly non-production** and pending security
review/revision before any wider exposure.

When corporate auth cutover resumes, use this checklist:

1. Flip security mode off development; refuse production + development mode.
2. Wire the approved `RequestContextProvider` (trusted ingress headers or
   successor). Confirm `subject` is stable per person/service.
3. Confirm Academy progress / workbench / challenge rows are keyed by real
   `subject` values.
4. Revoke / stop injecting the lab mock (development) principal.
5. Apply firewall / VPN rules for console + gateway (see security docs).
6. Verify fail-closed behavior when trusted context is missing.
7. Smoke: curriculum, lesson check, sandbox retrieve, mission check, cohort
   submit as a real principal.

### Progress migration notes

* When `database_url` is unset (typical unit tests): lesson / mission / challenge /
  workbench / quarantine state is **in-memory** (process-local).
* When `database_url` is set: SQLAlchemy stores persist
  `lesson_progress`, `mission_progress`, `challenge_submission`,
  `workbench_note`, and `corpus_quarantine` (see Alembic `0005_academy_durable`).
* **Practice sandboxes remain process-local** (ephemeral teaching desks; reset
  rebinds sandbox ids).
* Export for LMS/HR is later; never invent completion for other principals.

## Tracks

* **Foundations** — Gateway-only, aliases, streaming, honest readiness
* **Trust & evaluation** — hallucinations, citations, fail-closed schemas,
  honest errors
* **Site Ops** — broad DC ops spine (not retire-themed in public curriculum)
* **Support** — triage, knowledge RAG, structured intake, capstone mission
* **Security** — boundary, sanitized data, prompt-injection awareness
* **Product (PM)** — aliases, honest readiness, mission briefs
* **Managers** — what Yonder is, CPU vs GPU funding, trust over hype
* **Capacity** — CPU vs GPU literacy + real readiness panel (`/academy/capacity`)
* **Operator** — manifests, placement, alias lifecycle, lab live vs mock,
  admission honesty + `operator-readiness-loop` mission
* **Builder** — tools, RAG, fixture tools, structured fail-closed, embed/rerank,
  eval honesty + `builder-structured-ops-loop` / `ticket-tool-loop` missions

Lesson pages deep-link glossary terms to `/help#term-id` and offer **Explain in
Chat**. Completed tracks show an honest **status card** (progress counts only).

## Tier 2 scale adoption

### Cohorts & challenges

[`config/lab/cohorts.yaml`](../config/lab/cohorts.yaml) — time-boxed cohorts with
challenge rubrics. Completion is **criteria met / missed**, never a numeric
vanity score. Board lists principals who passed only.

### Instructor console

`/academy/instructor` — aggregated track completion, demo progress reset by
subject, corpus/preset quarantine (quarantined presets cannot create Practice
desks). Capability-gated.

### Workbench

`/academy/workbench` — principal-scoped notes + JSON export for mission evidence
and fixture tool outputs.

### Capacity panel

`GET /v1/lab/capacity` / `/academy/capacity` — alias readiness from Gateway route
state (`ready` / `not_ready` / `not_reported`) plus CPU-first vs GPU funding
guidance. Never invent green.

## Missions (workflow studio)

Authored multi-step flows under `config/lab/missions/`:

* `support-ticket-loop` — triage → Support sandbox RAG → structured intake
* `siteops-incident-loop` — SEL → fixture retrieve → incident → RMA
* `security-review-loop` — boundary → ops sandbox → tools allowlist
* `ticket-tool-loop` — fixture ticketing/runbook tools through Gateway
* `operator-readiness-loop` — Models → Capacity → Recipe readiness honesty
* `builder-structured-ops-loop` — fixture ticket → sandbox → structured → Workbench

Each mission records principal-scoped step completion and an `eval_case` check.

Fixture tools (Gateway allowlist only): `yonder_fixture_get_ticket`,
`yonder_fixture_get_runbook`.

## Practice sandboxes (department presets)

Presets: `site-ops-fixture`, `support-fixture`, `ops-fixture`, `eng-fixture`.
Quotas fail closed. Practice RAG never mixes into the shared Lab corpus. Never
paste production secrets. Instructors may quarantine presets.

## Prompt library

[`config/lab/prompts.yaml`](../config/lab/prompts.yaml) — **reviewed** patterns
only via `GET /v1/lab/prompts`. Drafts are not listed. Patterns link back to
lessons/Recipes for promote paths.

## Management demo script (~15 minutes)

1. `/academy` — **Site Ops live desk** CTA + capacity literacy one-liner.
2. Open `siteops-incident-loop` — SEL → retrieve → health → incident → RMA → Workbench.
3. Foundations 1–3 — Gateway-only → aliases → chat starter (if time).
4. Trust highlight — glossary chips + Explain in Chat; trust chips on replies.
5. Recipes — Today's picks; Save to Workbench; Run in Chat.
6. Cohorts — submit capacity-literacy; show criteria met (not a score).
7. Capacity panel — real alias readiness + CPU-first funding.
8. Instructor — class runner join URLs + lesson stall drill-down.
9. Workbench — Download evidence pack (principal-scoped JSON).
10. Close: CPU for Academy scale; GPUs for concurrent live try-it; never invent
    readiness. Status cards = progress only.


## Hard rules

* Console → Gateway only
* Never invent readiness, serials, sensors, warranty dates, customers, or SLAs
* Mocks labeled `training_scenario` / `not_supported` / `mock`
* No Yonder-owned login
* Challenge boards never invent ranks or vanity scores

See also [lab-training.md](./lab-training.md) and
[security-boundary.md](./security-boundary.md).
