# Evaluate Yonder

This is a reproducible local-development walkthrough for individuals, developers and prospective institutional evaluators. It is not a production setup or an independent user study.

## 1. Start and inspect

Follow [Quick Start](quickstart.md) on a trusted local machine. Open the Console and choose Get started. Confirm Gateway reachability, explicit runtime availability and hardware screening. Do not interpret a mock model or an unreported GPU as a real deployed model.

## 2. Demonstrate the transport without a model

From the local development machine, send a synthetic request through Gateway:

```bash
curl -fsS http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"yonder-chat","messages":[{"role":"user","content":"Synthetic demo fixture"}],"stream":false}'
```

A mock response demonstrates API routing only, not inference performance. For a real response, configure a supported provider and use the setup inference test; follow the [real model steps](quickstart.md#c-first-real-model-response-lm-studio-or-another-openai-compatible-server).

## 3. Inspect modular behavior

Open Operations to review worker and route status. Open Add-ons and inspect the bundled catalog. Planned or unavailable modules are not installable. On a disposable local deployment, activate Security Insights and inspect its read-only configuration guidance; removing it must not impair chat or delete conversations. This is not a compliance assessment.

## 4. Review evidence

The [verified onboarding browser run](https://github.com/dachakati-ux/Yonder-public/actions/runs/45934926528) includes actual desktop and mobile screenshots in its **setup-browser-screenshots** artifact. GitHub may require sign-in and artifacts have retention limits. Reproduce screenshots with the Console browser smoke workflow when needed. They contain only a disposable development environment; they are not lab or institutional screenshots.

The [release readiness checklist](release-readiness.md) distinguishes verified automated behavior from untested real models, independent beginner evaluations and production controls.

## 5. Report evaluation findings

Record the revision, OS and architecture, installation procedure, elapsed time to reach the Console and first actual inference, failed steps, accessibility observations, and whether assistance was needed. Record actual measurements; leave missing results blank. Use a synthetic prompt. Never attach user records, tokens, or private host details. Report defects using the repository's issue templates and security concerns privately.
