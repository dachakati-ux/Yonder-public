# Release readiness — evidence before claims

This is a gate checklist supplementing the [master roadmap](yonder-master-roadmap.md), not a replacement for milestones or a claim of completion. Each gate needs a commit SHA, dated test report, environment, and known limitations. A green CI run is not a production deployment.

| Area | Evidence required for a release-quality result | Milestone |
| --- | --- | --- |
| Product vision | Document audience and use cases; validate with independent users and educators | M06, M08 |
| Architecture | Core works with every optional service absent; API compatibility and add-on removal tests | M01, M02 |
| Feature breadth | End-to-end acceptance for each advertised capability, explicitly label unsupported runtimes | M01–M07 |
| Engineering | Clean CI, dependency audit, reproducible build, fault injection and tested migrations | M01, M08 |
| Documentation | Verified setup, screenshots, operational runbooks, feature/status matrix and release notes | M08 |
| Accessibility and onboarding | Independent novice installs on Linux, macOS and Windows/WSL; keyboard and screen-reader checks; first real model response | M01, M04, M08 |
| Production readiness | Approved upstream identity; cross-tenant and workspace isolation; hardened controller; offline backup/restore and upgrade rehearsal | M02, M03, M05, M08 |
| Portfolio and adoption | Public demo with synthetic data, versioned packages, clear license, issue/reporting process | M08 |

## Initial release gates

- [ ] Clean single-host source installation and published image installation tested on supported architectures.
- [ ] Local and remote inference endpoint canaries return actual model responses; unavailable hardware stays explicitly unavailable.
- [ ] All optional services disabled: Core boots, routes and chats. Enable and remove each reviewed add-on without unrelated data loss.
- [ ] SQLite and PostgreSQL backups restored onto isolated hosts; failure injection includes disk-full, interrupted migrations and stale credentials.
- [ ] Production ingress cannot be bypassed; development principals cannot reach production; independent tenant isolation and adversarial tests pass.
- [ ] Controller deployment avoids unrestricted shared host Docker access for untrusted workloads; signed release and revocation tested.
- [ ] Dependency vulnerabilities reviewed; release artifacts, SBOM and immutable versions published; reproducible verification documented.
- [ ] Independent beginner usability pilot and accessibility checks; screenshots and actual walkthrough published.
- [ ] Known limitations, support process and incident recovery published with a tagged release.

## Evidence record

For each gate record: date, commit SHA, test command or procedure, platform and runtime, observed result, artifact/log reference, reviewer, residual risk. Never store credentials, student data or private host details in public evidence. Leave unchecked if untested.

Track implementation in [M01](https://github.com/dachakati-ux/Yonder-public/issues/6) and [M08](https://github.com/dachakati-ux/Yonder-public/issues/13).

## Automated clean-host checks

The Clean-host Compose smoke workflow builds the local stack on a disposable Linux runner, checks non-default published ports, recreates the Gateway and checks SQLite persistence. It does not validate real model inference, macOS/Windows hardware, external identity, or production isolation. The archive tests verify SQLite contents, missing/corrupt database handling and symlink rejection; a full restore rehearsal remains a separate gate.

The local Compose smoke also checks browser CORS at the selected port and a connected worker reporting a Gateway-reachable address. A model endpoint canary remains mandatory for inference claims.

The Compose smoke workflow now also rehearses an actual local-volume archive and stages it outside the live container volume, checking the SQLite probe and required schema. This is a disposable-runner restore rehearsal, not an approved live rollback or PostgreSQL/add-on disaster-recovery test.
