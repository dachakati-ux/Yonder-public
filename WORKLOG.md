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

- Public evaluation and support: reproducible synthetic demo, verified browser artifact link, contributor guidance, security reporting policy and structured issue templates. No claims of independent user validation or stable production support.
