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
