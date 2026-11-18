"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { GatewayError, runLab } from "@/lib/gateway";
import type {
  LabAbilityId,
  LabGuidedMode,
  LabMediaKind,
  LabRunResult,
} from "@/types";
import {
  BUILD_PATHS,
  GUIDED_MODES,
  LAB_ABILITY_META,
  MEDIA_KINDS,
} from "./catalog";

export function AbilityWorkspace({ ability }: { ability: LabAbilityId }) {
  const meta = LAB_ABILITY_META[ability];
  const [draft, setDraft] = useState(meta.defaultPrompt);
  const [guidedMode, setGuidedMode] = useState<LabGuidedMode>("summarize");
  const [mediaKind, setMediaKind] = useState<LabMediaKind>("image");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LabRunResult | null>(null);

  const teaching = useMemo(() => result?.teaching ?? null, [result]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      const body = await runLab({
        ability,
        input: draft,
        guided_mode: ability === "guided" ? guidedMode : undefined,
        media_kind: ability === "generation" ? mediaKind : undefined,
      });
      setResult(body);
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}] (${err.requestId})`);
      } else {
        setError(err instanceof Error ? err.message : "Lab run failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">
            <Link href="/lab">Lab</Link> / {meta.title}
          </p>
          <h1>{meta.title}</h1>
          <p>{meta.promise}</p>
        </div>
        <div className="model-chip">
          {result ? `request: ${result.request_id}` : "gateway: /v1/lab/run"}
        </div>
      </header>

      <div className="lab-banner">
        {result
          ? `Last run mode=${result.mode} provider=${result.provider}`
          : "Runs prefer a live chat alias when READY; otherwise labeled training_scenario mocks."}
        {" · "}
        <Link href="/recipes">Open Recipes</Link>
        {" · "}
        <Link href="/academy">Used by Academy</Link>
        {" — never invent readiness; mocks stay labeled."}
      </div>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="lab-ability-grid">
        <article className="lab-panel">
          <h2>How it works</h2>
          <ol className="lab-steps-list">
            {(teaching?.how_it_works ?? defaultHow(ability)).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </article>

        <article className="lab-panel lab-panel-demo">
          <h2>Live demo</h2>
          {ability === "guided" ? (
            <div className="lab-mode-row">
              {GUIDED_MODES.map((mode) => (
                <button
                  key={mode.id}
                  type="button"
                  className="btn"
                  data-active={guidedMode === mode.id ? "true" : "false"}
                  onClick={() => setGuidedMode(mode.id)}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          ) : null}
          {ability === "generation" ? (
            <div className="lab-mode-row">
              {MEDIA_KINDS.map((kind) => (
                <button
                  key={kind.id}
                  type="button"
                  className="btn"
                  data-active={mediaKind === kind.id ? "true" : "false"}
                  onClick={() => setMediaKind(kind.id)}
                >
                  {kind.label}
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            className="lab-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={5}
            aria-label="Lab input"
            placeholder={
              ability === "generation"
                ? "Describe the image or video you want to generate…"
                : undefined
            }
          />
          <div className="conversation-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !draft.trim()}
              onClick={() => void onRun()}
            >
              {busy ? "Running…" : "Run through Gateway"}
            </button>
            {ability === "guided" ? (
              <Link
                className="btn btn-ghost"
                href={`/?prompt=${encodeURIComponent(draft)}&model=yonder-chat`}
              >
                Open in Chat
              </Link>
            ) : null}
            <Link className="btn btn-ghost" href="/recipes">
              Open as Recipe
            </Link>
          </div>

          {result ? (
            <div className="lab-result">
              <div className="lab-timeline">
                {result.steps.map((step, index) => (
                  <div key={`${String(step.type)}-${index}`} className="lab-step">
                    <div className="lab-step-type">{String(step.type)}</div>
                    {step.title ? (
                      <div className="entity-title">{String(step.title)}</div>
                    ) : null}
                    {step.detail ? (
                      <p className="entity-meta">{String(step.detail)}</p>
                    ) : null}
                    {step.name ? (
                      <p className="entity-meta">tool: {String(step.name)}</p>
                    ) : null}
                    {step.job_id ? (
                      <p className="entity-meta">job: {String(step.job_id)}</p>
                    ) : null}
                    {hasStepPayload(step) ? (
                      <pre className="logs-panel">
                        {JSON.stringify(pickStepPayload(step), null, 2)}
                      </pre>
                    ) : null}
                  </div>
                ))}
              </div>

              <div className="lab-answer">
                <h3>Answer</h3>
                <p>{result.answer}</p>
              </div>

              {result.assets?.length ? (
                <div className="lab-assets">
                  <h3>Generated assets</h3>
                  {result.assets.map((asset) => (
                    <div key={String(asset.asset_id)} className="lab-asset-card">
                      <div className="entity-title">
                        {asset.kind} · {asset.status}
                      </div>
                      <div className="entity-meta">
                        {asset.width}×{asset.height}
                        {asset.fps ? ` · ${asset.fps}fps` : ""}
                        {asset.duration_s ? ` · ${asset.duration_s}s` : ""}
                        {" · "}
                        {asset.source}
                      </div>
                      {asset.note ? (
                        <div className="lab-asset-note">{asset.note}</div>
                      ) : null}
                      {asset.preview_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          className="lab-asset-preview"
                          src={asset.preview_url}
                          alt={`${asset.kind} training placeholder`}
                        />
                      ) : null}
                      {asset.frames?.length ? (
                        <div className="lab-storyboard">
                          {asset.frames.map((frame) => (
                            <div
                              key={String(frame.index)}
                              className="lab-storyboard-frame"
                            >
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img
                                src={frame.preview_url}
                                alt={`Frame ${frame.index}`}
                              />
                              <span>t={frame.timecode_s}s</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}

              {result.citations?.length ? (
                <div className="lab-citations">
                  <h3>Citations</h3>
                  {result.citations.map((citation) => (
                    <div key={String(citation.id)} className="entity-card">
                      <div className="entity-title">
                        [{citation.id}] {citation.title}
                      </div>
                      <div className="entity-meta">{citation.snippet}</div>
                    </div>
                  ))}
                </div>
              ) : null}

              {result.structured ? (
                <div className="lab-structured">
                  <h3>Structured · {result.structured.schema_id}</h3>
                  <pre className="logs-panel">
                    {JSON.stringify(result.structured.data, null, 2)}
                  </pre>
                </div>
              ) : null}
            </div>
          ) : null}
        </article>

        <article className="lab-panel">
          <h2>How to build</h2>
          <ol className="lab-steps-list">
            {(teaching?.how_to_build ?? defaultBuild(ability)).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
          <div className="entity-meta" style={{ marginTop: "1rem" }}>
            Start in-repo:
          </div>
          <ul className="lab-path-list">
            {BUILD_PATHS[ability].map((path) => (
              <li key={path}>
                <code>{path}</code>
              </li>
            ))}
          </ul>
        </article>
      </section>
    </div>
  );
}

function hasStepPayload(step: Record<string, unknown>): boolean {
  return [
    "arguments",
    "result",
    "data",
    "schema",
    "chunks",
    "context_preview",
    "content",
    "asset",
    "progress",
  ].some((key) => key in step && step[key] !== undefined);
}

function pickStepPayload(step: Record<string, unknown>): Record<string, unknown> {
  const keys = [
    "arguments",
    "result",
    "data",
    "schema",
    "chunks",
    "context_preview",
    "content",
    "query",
    "valid",
    "tool",
    "asset",
    "progress",
  ] as const;
  const out: Record<string, unknown> = {};
  for (const key of keys) {
    if (key in step && step[key] !== undefined) {
      // Avoid dumping huge data URLs inside the timeline JSON.
      if (key === "asset" && typeof step.asset === "object" && step.asset) {
        const asset = { ...(step.asset as Record<string, unknown>) };
        if (typeof asset.preview_url === "string" && asset.preview_url.length > 120) {
          asset.preview_url = `${asset.preview_url.slice(0, 64)}…`;
        }
        if (Array.isArray(asset.frames)) {
          asset.frames = asset.frames.map((frame) => {
            if (!frame || typeof frame !== "object") return frame;
            const copy = { ...(frame as Record<string, unknown>) };
            if (typeof copy.preview_url === "string" && copy.preview_url.length > 120) {
              copy.preview_url = `${copy.preview_url.slice(0, 64)}…`;
            }
            return copy;
          });
        }
        out.asset = asset;
        continue;
      }
      out[key] = step[key];
    }
  }
  return out;
}

function defaultHow(ability: LabAbilityId): string[] {
  if (ability === "tools") {
    return [
      "Decide if live data is required.",
      "Emit a structured tool call.",
      "Observe the tool result.",
      "Answer only from that result.",
    ];
  }
  if (ability === "rag") {
    return [
      "Retrieve trusted chunks.",
      "Augment the prompt.",
      "Generate with citations.",
      "Fail closed when context is empty.",
    ];
  }
  if (ability === "structured") {
    return [
      "Publish a JSON schema.",
      "Ask the model for that shape only.",
      "Validate before use.",
      "Retry or fail closed on invalid output.",
    ];
  }
  if (ability === "generation") {
    return [
      "Normalize the creative brief.",
      "Enqueue an image or video job on the Gateway.",
      "Sample / decode on a generation worker.",
      "Return asset metadata and previews through the Gateway only.",
    ];
  }
  return [
    "Pick a guided mode (system prompt).",
    "Run the same chat pipeline.",
    "Treat prompt design as the product surface.",
  ];
}

function defaultBuild(ability: LabAbilityId): string[] {
  return [
    `Extend the ${ability} scenario in lab_scenarios.py.`,
    "Keep demos gateway-mediated — never call workers from the console.",
    "Label mock training scenarios until a real runtime is online.",
  ];
}
