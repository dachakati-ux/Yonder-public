"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  GatewayError,
  createGenerationJob,
  deleteGenerationJob,
  getGenerationJob,
  listGenerationJobs,
  type GenerationJob,
  uploadGenerationReference,
  waitForGenerationJob,
} from "@/lib/gateway";
import { GATEWAY_URL } from "@/lib/brand";
import {
  LoadProgressBar,
  latestGenerationMessage,
  latestGenerationPercent,
} from "@/components/LoadProgressBar";
import type {
  LabAspectRatio,
  LabGenerationMode,
  LabStylePreset,
} from "@/types";

function artifactHref(url: string): string {
  return url.startsWith("http") ? url : `${GATEWAY_URL}${url}`;
}

const MODES: Array<{ id: LabGenerationMode; label: string; hint: string }> = [
  { id: "txt2img", label: "Text → Image", hint: "Prompt only" },
  { id: "img2img", label: "Image → Image", hint: "Reference + strength" },
  { id: "txt2video", label: "Text → Video", hint: "Prompt → motion" },
  { id: "image2video", label: "Image → Video", hint: "Still → storyboard" },
];

const ASPECTS: LabAspectRatio[] = ["1:1", "16:9", "9:16", "4:3", "3:4"];

const STYLES: Array<{ id: LabStylePreset; label: string }> = [
  { id: "yonder-brand", label: "Yonder brand" },
  { id: "photoreal", label: "Photoreal" },
  { id: "illustration", label: "Illustration" },
  { id: "none", label: "None" },
];

export function StudioWorkspace() {
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<LabGenerationMode>("txt2img");
  const [prompt, setPrompt] = useState(
    "Chrome Y emblem over a dark hexagonal grid, electric cyan rim light",
  );
  const [negative, setNegative] = useState("blurry, watermark, text artifacts");
  const [aspect, setAspect] = useState<LabAspectRatio>("16:9");
  const [style, setStyle] = useState<LabStylePreset>("yonder-brand");
  const [steps, setSteps] = useState(28);
  const [guidance, setGuidance] = useState(7);
  const [strength, setStrength] = useState(0.55);
  const [seed, setSeed] = useState<string>("");
  const [referenceId, setReferenceId] = useState<string | null>(null);
  const [referenceName, setReferenceName] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [history, setHistory] = useState<GenerationJob[]>([]);

  const showReference = mode === "img2img" || mode === "image2video";

  useEffect(() => {
    void listGenerationJobs()
      .then(setHistory)
      .catch(() => {
        /* history is best-effort */
      });
  }, []);

  useEffect(() => {
    const jobId = searchParams.get("job");
    if (!jobId) return;
    let cancelled = false;
    (async () => {
      try {
        const existing = await getGenerationJob(jobId);
        if (!cancelled) setJob(existing);
      } catch {
        /* deep-link is best-effort */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [searchParams]);

  async function onUpload(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const artifact = await uploadGenerationReference(file);
      setReferenceId(artifact.id);
      setReferenceName(artifact.filename);
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}]`);
      } else {
        setError(err instanceof Error ? err.message : "Upload failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      const parsedSeed = seed.trim() === "" ? null : Number(seed);
      const created = await createGenerationJob({
        mode,
        prompt,
        negative_prompt: negative || undefined,
        aspect_ratio: aspect,
        style_preset: style === "none" ? null : style,
        steps,
        guidance,
        strength: showReference ? strength : null,
        seed: Number.isFinite(parsedSeed as number) ? (parsedSeed as number) : null,
        reference_artifact_ids:
          showReference && referenceId ? [referenceId] : [],
      });
      setJob(created);
      setHistory((prev) => [created, ...prev.filter((item) => item.id !== created.id)].slice(0, 12));
      const finished = await waitForGenerationJob(created.id, {
        // EnsureReady + Diffusers load can exceed a few minutes.
        maxAttempts: 400,
        intervalMs: 1500,
        onUpdate: (live) => {
          setJob(live);
          setHistory((prev) =>
            [live, ...prev.filter((item) => item.id !== live.id)].slice(0, 12),
          );
        },
      });
      setJob(finished);
      setHistory((prev) =>
        [finished, ...prev.filter((item) => item.id !== finished.id)].slice(0, 12),
      );
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}] (${err.requestId})`);
      } else {
        setError(err instanceof Error ? err.message : "Generation failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lab-layout studio-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">Generation Studio</p>
          <h1>Create through the Gateway</h1>
          <p>
            Durable jobs, uploads, and policy all go through{" "}
            <code>/v1/generation/*</code>. Job history is Gateway-persisted per
            principal and survives restarts.
          </p>
        </div>
        <div className="model-chip">
          {job ? `job · ${job.id}` : "gateway: /v1/generation/jobs"}
        </div>
      </header>

      <div className="lab-banner">
        Product job API · availability remains not_supported until a generation
        runtime is selected after host evidence
      </div>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="studio-grid">
        <article className="lab-panel studio-controls">
          <h2>Brief</h2>
          <div className="lab-mode-row">
            {MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                className="btn"
                data-active={mode === item.id ? "true" : "false"}
                onClick={() => setMode(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <label className="studio-field">
            <span>Prompt</span>
            <textarea
              className="lab-input"
              rows={5}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
          </label>

          <label className="studio-field">
            <span>Negative prompt</span>
            <textarea
              className="lab-input"
              rows={2}
              value={negative}
              onChange={(event) => setNegative(event.target.value)}
            />
          </label>

          {showReference ? (
            <label className="studio-field">
              <span>Reference upload</span>
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml"
                onChange={(event) => void onUpload(event.target.files?.[0] ?? null)}
              />
              {referenceId ? (
                <span className="entity-meta">
                  Uploaded {referenceName} ({referenceId})
                </span>
              ) : (
                <span className="entity-meta">Optional reference artifact via Gateway</span>
              )}
            </label>
          ) : null}

          <div className="studio-controls-row">
            <label className="studio-field">
              <span>Aspect</span>
              <select
                className="status-select"
                value={aspect}
                onChange={(event) => setAspect(event.target.value as LabAspectRatio)}
              >
                {ASPECTS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="studio-field">
              <span>Style</span>
              <select
                className="status-select"
                value={style}
                onChange={(event) => setStyle(event.target.value as LabStylePreset)}
              >
                {STYLES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="studio-controls-row">
            <label className="studio-field">
              <span>Steps ({steps})</span>
              <input
                type="range"
                min={4}
                max={60}
                value={steps}
                onChange={(event) => setSteps(Number(event.target.value))}
              />
            </label>
            <label className="studio-field">
              <span>Guidance ({guidance})</span>
              <input
                type="range"
                min={1}
                max={20}
                step={0.5}
                value={guidance}
                onChange={(event) => setGuidance(Number(event.target.value))}
              />
            </label>
          </div>

          {showReference ? (
            <label className="studio-field">
              <span>Strength ({strength.toFixed(2)})</span>
              <input
                type="range"
                min={0.05}
                max={1}
                step={0.05}
                value={strength}
                onChange={(event) => setStrength(Number(event.target.value))}
              />
            </label>
          ) : null}

          <label className="studio-field">
            <span>Seed (blank = derived)</span>
            <input
              className="status-select"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
              placeholder="optional"
            />
          </label>

          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !prompt.trim()}
            onClick={() => void onGenerate()}
          >
            {busy ? "Submitting…" : "Create generation job"}
          </button>
        </article>

        <article className="lab-panel studio-preview">
          <h2>Job</h2>
          {!job ? (
            <div className="empty-state">
              <h3>Ready when you are</h3>
              <p>Jobs are durable on the Gateway. Workers never see the browser.</p>
            </div>
          ) : (
            <div className="lab-result">
              <div className="lab-answer">
                <h3>
                  {job.status} · {job.availability}
                </h3>
                <p>
                  Provider <code>{job.provider}</code>
                  {job.error ? ` — ${job.error}` : ""}
                </p>
              </div>

              {["queued", "running", "accepted"].includes(job.status) ? (
                <LoadProgressBar
                  percent={latestGenerationPercent(job.progress)}
                  label={job.status}
                  detail={latestGenerationMessage(
                    job.progress,
                    "Waiting for worker progress…",
                  )}
                />
              ) : null}

              {job.assets?.length ? (
                job.assets.map((asset) => {
                  const href = artifactHref(asset.url);
                  const video =
                    asset.mime_type.startsWith("video/") ||
                    asset.kind === "video";
                  return (
                    <div key={asset.artifact_id} className="lab-asset-card">
                      <div className="entity-title">
                        {asset.kind} · {asset.mime_type}
                      </div>
                      {video ? (
                        <video className="studio-media" src={href} controls playsInline />
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          className="studio-media"
                          src={href}
                          alt={job.prompt.slice(0, 120)}
                        />
                      )}
                      <div className="message-actions">
                        <a className="btn btn-ghost" href={href} download>
                          Download
                        </a>
                        <a className="entity-meta" href={href}>
                          {asset.url}
                        </a>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="entity-meta">
                  {["queued", "running", "accepted"].includes(job.status)
                    ? "Waiting for runtime assets…"
                    : "No runtime assets yet — expected while generation runtime is not_supported."}
                </div>
              )}

              <div className="lab-timeline">
                {job.progress.map((step, index) => (
                  <div key={`${step.step}-${index}`} className="lab-step">
                    <div className="lab-step-type">{step.step}</div>
                    <p className="entity-meta">{step.message}</p>
                    {typeof step.percent === "number" ? (
                      <p className="entity-meta">{step.percent}%</p>
                    ) : null}
                  </div>
                ))}
              </div>

              <pre className="logs-panel">{JSON.stringify(job, null, 2)}</pre>
            </div>
          )}
        </article>

        <article className="lab-panel studio-history">
          <h2>Job history</h2>
          {history.length === 0 ? (
            <div className="entity-meta">Gateway-persisted jobs for this principal appear here.</div>
          ) : (
            <div className="stack">
              {history.map((item) => {
                const thumb = item.assets?.find((asset) =>
                  asset.mime_type.startsWith("image/"),
                );
                return (
                  <div
                    key={item.id}
                    className="entity-card"
                    style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}
                  >
                    <button
                      type="button"
                      className="entity-button"
                      onClick={() => {
                        void (async () => {
                          try {
                            const live = await getGenerationJob(item.id);
                            setJob(live);
                            setHistory((prev) =>
                              [live, ...prev.filter((row) => row.id !== live.id)].slice(
                                0,
                                12,
                              ),
                            );
                          } catch {
                            setJob(item);
                          }
                        })();
                      }}
                      style={{
                        display: "flex",
                        gap: "0.75rem",
                        alignItems: "center",
                        flex: 1,
                        background: "transparent",
                        border: 0,
                        padding: 0,
                        textAlign: "left",
                        cursor: "pointer",
                        color: "inherit",
                      }}
                    >
                      {thumb ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          className="studio-thumb"
                          src={artifactHref(thumb.url)}
                          alt=""
                        />
                      ) : null}
                      <div>
                        <div className="entity-title">
                          {item.mode} · {item.status}
                        </div>
                        <div className="entity-meta">{item.prompt.slice(0, 80)}</div>
                      </div>
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`Delete job ${item.id}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        void (async () => {
                          try {
                            await deleteGenerationJob(item.id);
                            setHistory((prev) =>
                              prev.filter((row) => row.id !== item.id),
                            );
                            setJob((current) =>
                              current?.id === item.id ? null : current,
                            );
                          } catch (err) {
                            if (err instanceof GatewayError) {
                              setError(`${err.message} [${err.code}]`);
                            } else {
                              setError(
                                err instanceof Error
                                  ? err.message
                                  : "Delete failed",
                              );
                            }
                          }
                        })();
                      }}
                    >
                      Delete
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </article>
      </section>
    </div>
  );
}
