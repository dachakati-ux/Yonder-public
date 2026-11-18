# AGENTS.md — Yonder

**Yonder** — Intelligence beyond search.

This file governs all development agents working in this repository.

## Project identity

Yonder is a **standalone** internal AI platform repository.

* Do **not** introduce dependencies on unrelated codebases.
* Do **not** reference, import, fork, or copy from unrelated projects.
* Do **not** invent product scope beyond the documented mission.

## Non-negotiable rules

1. **No fabricated data.** Never invent GPU metrics, worker state, model readiness, latencies, or operational events. If a value is unavailable, report an explicit state (`not_supported`, `not_reported`, `permission_unavailable`, `worker_disconnected`, `collection_failed`).
2. **Gateway only.** No UI or internal application may call an inference runtime or GPU worker directly. All inference traffic goes through Yonder Gateway.
3. **No authentication system.** Do not build login pages, password handling, user registration, local production user databases, corporate token issuance, permanent API-key bypasses, hidden administrator credentials, or hardcoded employees/groups.
4. **Preserve the corporate security boundary.** Authentication and authorization happen upstream. Yonder accepts only trusted, normalized `RequestPrincipal` context through a replaceable `RequestContextProvider`.
5. **Do not guess upstream IAM (or other corporate IAM) details.** Integrate via the provider contract when the approved mechanism is known.
6. **Development identity injection stays isolated.** Development security mode must never activate automatically, never be the production default, and must refuse startup when `environment=production` and `security.mode=development`.
7. **Prefer adapters and typed contracts** over hardcoded runtime, worker, or hardware logic.
8. **Treat the V100 as the first worker, not the permanent architecture.** Preserve multi-worker and multi-GPU expansion paths.
9. **Lab worker parity.** When adding a new model family, runtime, or ability (chat, embed, rerank, Diffusers image/video, tools, etc.), wire **every** registered lab worker (`worker-ai1`, `worker-ai3`, `worker-ai7`, …) — not just the bring-up host. That includes local runtime deps on each node, readable model bundles, a **writable** per-host output/cache path when NFS is read-only, node/env config, and a canary or load smoke on each worker before calling the ability “lab-ready.” An ai1-only path is a temporary spike; close parity before declaring done.
10. **Record meaningful work in `WORKLOG.md`.**
11. **Update documentation** when architecture or configuration changes.
12. **Add tests** for lifecycle and routing changes.
13. **Do not silently weaken failures or security controls** to make tests pass.

## Working pattern

Every meaningful assignment must end with:

```text
Completed
In progress
Next
Risks or blockers
Validation performed
```

### Console / UI changes — always push to lab

When you change **console UI** (`apps/console/**`, styles, Academy/Lab pages,
etc.), **do not stop at local-only**. Always:

1. Stage the console build (`scripts/stage-console-build.sh` with
   `NEXT_PUBLIC_YONDER_GATEWAY_URL=http://127.0.0.1:8080`).
2. Sync to ai1 (`yonder-src`) and run
   `scripts/deploy-ai1-verified-runtime.sh` (or equivalent console install +
   `yonder-console` restart).
3. Tell the operator to **hard-refresh** the browser (Cmd+Shift+R).

Local `npm run dev` is fine for iteration, but the assignment is incomplete until
the lab console is updated — that is where demos and review happen.

## Architecture reminders

* Clients use stable model aliases, never worker names, GPU indexes, or filesystem paths.
* Inference runtimes sit behind `InferenceProvider` adapters.
* Workers register with the control plane; the control plane does not SSH for normal operations.
* Configuration layers: application defaults → environment config → node config → environment variables / secret references.
* Production fails closed when trusted request context cannot be established.

## Phase discipline

Do not jump ahead of documented phases without collecting required host/runtime evidence.
In particular: do not select or install a model runtime until V100 host, driver, CUDA, storage, and compatibility details are documented.

## Security reminder (verbatim)

> Yonder does not authenticate corporate users. Authentication and authorization occur in an approved upstream corporate security layer. Yonder accepts only trusted, normalized request context through a replaceable adapter and must not be directly exposed to ordinary clients in production.
