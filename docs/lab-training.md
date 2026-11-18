# Training Lab

The Yonder Training Lab teaches local-AI patterns through the Gateway: how they
work, how to use them, and how to build them. Chat is one ability among many.

**Academy** (`/academy`) adds ordered role curricula (Foundations, Trust, Site
Ops, Support, Security, Product, Managers, Operator, Builder), principal-scoped
progress, workflow missions, department Practice desks, and a governed prompt
library on top of Lab / Recipes / Help. See [academy.md](./academy.md).

## Surface

* Console: `/lab` gallery and ability pages under `/lab/{tools,rag,structured,guided,generation}`
* Wave 2 playgrounds: `/lab/embeddings`, `/lab/rerank`, `/lab/compare`, `/lab/eval`
* Academy: `/academy`, `/academy/lessons/[id]`, `/academy/sandbox`
* Ops Recipes: `/recipes` (curated company-workflow starters → Chat/Lab/curl)
* Helper search + glossary: topbar search (logo-adjacent) and `/help`
* Gateway: `GET /v1/lab/abilities`, `POST /v1/lab/run`, Academy curriculum/progress/sandbox under `/v1/lab/*`
* Capability: `lab.use` (granted to the lab mock / development principal —
  lab only, pending corporate auth review)

## Helper search

See [help-glossary.md](./help-glossary.md). The topbar helper matches authored
terms and in-app routes; the **AI** control deep-links into Chat and never
fabricates definitions or hardware state in the dropdown.

## Recipes (ops proof)

`/recipes` is the trainee + builder gallery for proving how AI streamlines
operations (ticket triage, runbook Q&A, structured intake, doc rerank, code
assist, meeting actions). Cards deep-link into Chat with prompt/model/system
query params, link to Lab/playgrounds, and expose **Copy curl** for Gateway
`/v1` calls. Readiness badges use route/deployment state or `/v1/models` —
never invent a successful demo.

## Wave 1 abilities

| Ability | Trainee learns |
| --- | --- |
| Tool use | Plan → tool_call → tool_result → grounded answer |
| RAG | Retrieve → augment → generate with citations |
| Structured output | Schema → JSON → validate fail-closed |
| Guided modes | Productized system prompts (summarize / critique / explain / coach) |
| AI generation | Image/video jobs: prompt → queue → sample → asset (Diffusers on ai1 after canary PASS) |

## Live vs mock

`config/lab/runtime.yaml` maps abilities to chat aliases (default `yonder-chat`).
When the alias resolves to a READY deployment, `/v1/lab/run` executes **live**
(`mode: live`, `provider: gateway_chat`) for tools / RAG / structured / guided.
Otherwise it falls back to labeled `training_scenario` mocks. Generation stays
mock / not_supported until a generation worker exists. Lab corpus includes ops
teaching docs (change windows, on-call, access policy) in
`config/lab/corpus.yaml`.

## Generation Studio and Chat Generate

Console route `/studio` is the **advanced** product surface for generative
controls (txt2img, img2img, image→video, aspect, seed, negatives, style,
reference upload). Chat also exposes an explicit **Image** / **Video** composer
mode that creates the same durable Gateway jobs and shows the result inline in
the thread (no multimodal chat-completion pretending).

Both surfaces call:

* `POST /v1/generation/uploads`
* `POST /v1/generation/jobs`
* `GET /v1/generation/jobs` / `GET /v1/generation/jobs/{id}`
* `GET /v1/generation/artifacts/{id}`

When `diffusers.enabled` is true and the worker has CUDA torch/Diffusers
installed, jobs route to the worker Diffusers runtime (Z-Image, SDXL,
CogVideoX, Wan). Chat Generate requires a ready `yonder-image` or
`yonder-video` alias (bound when a Diffusers deployment loads). Otherwise jobs
still return `status=not_supported` / `availability=not_supported` from the mock
generation provider. Lab `/lab/generation` remains the teaching scenario
(`POST /v1/lab/run`).

Conversation messages may carry optional JSON `metadata` (kind `generation`) so
reloaded chats keep artifact references. Studio deep-link: `/studio?job={id}`.

Operator rule: Diffusers and llama.cpp cannot share a GPU on the same worker.

Platform building blocks also used by Lab:

* Tools → `app.services.tools.ToolExecutor` / `/v1/platform/tools*`
* RAG → durable lexical store / `/v1/platform/rag*`
* Structured → `SchemaRegistry` / `/v1/platform/schemas`
* Embeddings → `/v1/embeddings`

## Mock vs real runtime

Lab keeps explicit `training_scenario` mocks in
`apps/gateway/app/services/lab_scenarios.py` as the fail-closed fallback.
Live execution is implemented in `apps/gateway/app/services/lab_live.py` and
selected when the configured alias is READY. Responses always label
`mode` / `provider` honestly. Console stays gateway-only.

## Security

* All Lab traffic goes through Yonder Gateway.
* Console never calls workers, tools, or runtimes directly.
* Lab requires `lab.use` on the trusted `RequestPrincipal`.

## Wave 2 playgrounds (shipped)

* Embeddings playground — `/lab/embeddings`
* Rerank sandbox — `/lab/rerank`
* Side-by-side compare — `/lab/compare`
* Light eval / quiz pack — `/lab/eval`

Vision understanding and real diffusion backends remain future work (see
`docs/future-a100-expansion.md`).
