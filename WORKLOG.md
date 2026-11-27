# Yonder Worklog

## Open-Source Release

* Sanitized configuration, documentation, and scripts for public open-source release.
* Added `./start.sh` one-command Docker launcher with dependency checks and novice-friendly setup documentation.

## 2026-11-26 — Release hardening (M01.2, M01.5, M08)

* Require health-aware Compose v2 startup; report actual loopback ports and never shell-source dotenv.
* Reject production configuration in the local launcher; clarify the trusted-ingress boundary.
* Fail closed on unsupported live database/mode or additional services during convenience updates; disable unsafe live rollback.
* Add launcher regression tests and a measurable release gate checklist. Live Docker, restore, hardware, and institutional testing remain outstanding.

## 2026-11-26 — Release hardening follow-up

* Corrected malformed update-script substitution; quiesce local stack before backup, consolidate SQLite WAL and check integrity, preserve partial archives separately.
* Include uv workspace member manifests in container builds, require locked sync, and avoid re-resolving dependencies during migrations.
* Exclude local credentials, runtime data and build outputs from Docker contexts.
* Add clean-host Compose smoke CI with non-default ports and database persistence probe; add archive regression tests. Real runner results and independent restore remain required.

* Add development-only configured console origin to CORS; advertise the worker's reachable Docker DNS URL; disable mock speech in the default Compose stack; document exact external runtime attachment and inference acceptance. Four archive-helper regression tests passed in a local Python 3.13 environment using matching source; full repository and Docker validation remain CI-dependent.

* Add isolated local restore staging with exclusive destination creation, path/link/duplicate checks and SQLite integrity verification. Extend Compose smoke to rehearse archive creation and staged restore. Local Python validation: 11 archive/restore cases passed (including WAL, corrupt archives, traversal, symlink and overwrite prevention); actual Compose rehearsal remains pending CI.

## 2026-11-26 — Distribution and public feature-status preparation

* Add explicit versioned prebuilt image overlay and local launcher with no implicit latest tag; preserve source-build path and local-only security boundary.
* Add image-only candidate build/smoke and separately permissioned, explicit-tag publication workflow. No package publication or release creation was performed.
* Reconcile stale design/draft language with the source tree and document unavailable add-ons and real runtime requirements. Keep all catalog activation states unchanged. Candidate CI, native ARM, signed releases and public registry pull tests remain pending.

## Guided first-run setup (M01/M04/M08)

* Add principal-scoped setup diagnostics distinguishing mock, unavailable and reported-ready model routes; add regression tests. Live model and lab verification remain required.

* Implement standalone Get started UI with principal-scoped model diagnostics, single-device observed memory screening, explicit inference test, skip navigation and responsive styles. Add TypeScript regression tests. Lab deployment and independent browser review remain outstanding.

* Share the conservative single-device memory screening helper with Model Library. Add real Chromium local setup smoke, keyboard and scoped axe checks, mobile/desktop captures, and explicit fixture-only inference success/error UI checks. No real-inference claim.

## Production trust hardening (M03/M08)

* Fail closed in production trusted-header mode unless distinct managed ingress and worker secrets are configured; reject ambiguous header names, duplicate ingress authenticator headers and untrusted worker registration. Add configuration and middleware negative tests. Document migration and the required proxy stripping/network controls; no upstream IAM assumptions or production certification.

* Add scheduled and PR dependency audits with machine-readable evidence; require a same-commit passing main audit for image publication. No dependency findings have been certified clean before the workflow executes.

- Dependency audit remediation: refreshed Core certificate bundle, Console Next.js/Vitest and vulnerable transitive dependencies; added a hash-pinned standalone controller requirements lock and upgraded cryptography to 50.x. Regenerated locks in an isolated GitHub Actions job and removed the temporary lock-generation workflow. Full verification and security re-audit required before merge.

- Add-on lifecycle integrity: distinguish stored installations from usable activation; fail closed on version drift, incompatible releases and transitive dependency cycles, retain state for operator review, and cover API mount/report/health behavior with regression tests. CI verification pending.

- PostgreSQL recovery rehearsal: disposable real database, API-generated synthetic fixture, quiesced dump, distinct database restore, Gateway restart and read/write verification with source database preservation. CI pending; separate-host disaster recovery remains outstanding.

- PostgreSQL rehearsal exposed revision 0006 exceeding Alembic's default 32-character version column. Widened that column before recording the existing revision on PostgreSQL; retained published IDs and SQLite behavior. Rerun pending.

- Public evaluation and support: reproducible synthetic demo, verified browser artifact link, contributor guidance, security reporting policy and structured issue templates. No claims of independent user validation or stable production support.

## Real CPU inference acceptance

- Add isolated actual llama.cpp CPU model canary using checksum-verified SmolLM2 weights, Gateway-only API checks, measured latency evidence, streaming completion and outage refusal. No lab GPU readiness or model quality claim; CI pending.

- Actual CPU run 45984246631 passed streaming and nonstreaming (first content 0.022 s, full stream 0.247 s, nonstream 0.297 s) and refused a stopped runtime with HTTP 502. Pin its exact model commit, weight hash, byte count and runtime digest from the evidence artifact. Rerun the pinned fixture before treating it as repeatable evidence.
