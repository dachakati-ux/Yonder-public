# Yonder Worklog

## Open-Source Release

* Sanitized configuration, documentation, and scripts for public open-source release.
* Added `./start.sh` one-command Docker launcher with dependency checks and novice-friendly setup documentation.

## 2026-11-26 — Release hardening (M01.2, M01.5, M08)

* Require health-aware Compose v2 startup; report actual loopback ports and never shell-source dotenv.
* Reject production configuration in the local launcher; clarify the trusted-ingress boundary.
* Fail closed on unsupported live database/mode or additional services during convenience updates; disable unsafe live rollback.
* Add launcher regression tests and a measurable release gate checklist. Live Docker, restore, hardware, and institutional testing remain outstanding.
