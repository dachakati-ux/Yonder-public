# Help glossary and helper search

The console topbar includes a **helper search** (right of the Yonder logo) plus
an **AI** control. Default behavior is internal: glossary terms and light in-app
navigation hits. The AI button does not answer in the dropdown — it opens Chat
with the current query via the existing deep-link pattern.

## Surfaces

* Topbar helper: `HelperSearch` in `AppShell` (Brand | Helper + AI | Nav)
* Full library: `/help` (deep links like `/help#ready`)
* Shared authored source: `config/help/glossary.yaml`
* Gateway: `GET /v1/help/glossary` (loads the YAML)
* Console offline fallback: `apps/console/src/features/help/glossaryData.ts`

YAML is the source of truth. Console prefers the Gateway endpoint and falls back
to the bundled TypeScript mirror when Gateway is unreachable — it never invents
terms.

## Term shape

Each glossary entry has:

* `id` — stable slug (used in `/help#id`)
* `term` — display name
* `aliases[]` — alternate spellings / IDs
* `short` — one-line summary
* `body` — fuller definition (authored; never invent live hardware state)
* `related[]` — other term ids
* `links[]` — console paths or docs (`label`, `href`)

## Search behavior

* Case-insensitive match on `term`, `aliases`, and `short`/`body`
* Ranking: exact term > alias > substring
* App hits come from a static catalog (primary nav + Lab playgrounds + `/help`)
* Keyboard: `/` focuses search; `Enter` opens top term; `Cmd/Ctrl+Enter` sends
  to Chat; `Esc` closes

## AI button

`buildHelperAiHref` produces `/?prompt=Explain in Yonder terms: …&model=yonder-chat&recipe=helper-ai`.
Chat uses Gateway like any other starter. If `yonder-chat` is not READY, the user
sees the normal Chat/gateway error — the helper panel must not invent answers.
When gauntlet/measurement data is in and a dedicated GPU is assigned for chat,
point the `yonder-chat` alias at that deployment; the AI helper path stays the same.

## Adding a term

1. Append the entry to `config/help/glossary.yaml`
2. Mirror it in `glossaryData.ts` (offline fallback)
3. Optionally add app-catalog keywords in `appCatalog.ts`
4. Run gateway pytest `test_help_glossary.py` and console vitest
  (`features/help/search.test.ts`)
