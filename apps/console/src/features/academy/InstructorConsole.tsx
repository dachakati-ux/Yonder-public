"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  GatewayError,
  getInstructorSummary,
  instructorCorpusAction,
  instructorResetProgress,
  type InstructorSummary,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";
import { InstitutionPanel } from "./InstitutionPanel";

const PRESET_CORPORA = [
  "site-ops-fixture",
  "support-fixture",
  "ops-fixture",
  "eng-fixture",
];

export function InstructorConsole() {
  const [data, setData] = useState<InstructorSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [resetSubject, setResetSubject] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expandedTrack, setExpandedTrack] = useState<string | null>(null);

  async function load() {
    try {
      const body = await getInstructorSummary();
      setData(body);
      setError(null);
      setForbidden(false);
    } catch (err) {
      if (err instanceof GatewayError) {
        const denied =
          err.code === "FORBIDDEN" ||
          err.code === "permission_denied" ||
          /403|forbid|capability|instruct/i.test(`${err.code} ${err.message}`);
        setForbidden(denied);
        setError(
          denied
            ? "Instructor console needs the academy.instruct capability (or operations.manage). Ask an operator to grant it for your principal — Yonder does not create accounts."
            : `${err.message} [${err.code}]`,
        );
      } else {
        setError(err instanceof Error ? err.message : "Failed to load instructor summary");
      }
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const exportPayload = useMemo(() => {
    if (!data) return null;
    return JSON.stringify(data, null, 2);
  }, [data]);

  async function onReset() {
    if (!resetSubject.trim()) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Reset Academy progress for subject "${resetSubject.trim()}"?`,
      )
    ) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await instructorResetProgress(resetSubject.trim());
      setMessage(
        `Cleared ${result.cleared} progress row(s) for ${result.principal_subject} (by ${result.reset_by}) at ${new Date().toISOString()}.`,
      );
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCorpus(corpusId: string, action: "quarantine" | "release") {
    setBusy(true);
    setMessage(null);
    try {
      const result = await instructorCorpusAction({ corpus_id: corpusId, action });
      setMessage(
        `${action} ${corpusId}. Quarantined: ${result.quarantined.join(", ") || "(none)"}`,
      );
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Corpus action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="academy-layout">
      <AcademySubnav />
      <header className="academy-lesson-hero academy-workspace-hero">
        <p className="empty-kicker academy-hero-kicker">Instructor</p>
        <h1>Enablement console</h1>
        <p className="academy-lesson-summary">
          Aggregated track completion and demo controls. Requires{" "}
          <code>academy.instruct</code> (or <code>operations.manage</code>).
        </p>
        {exportPayload ? (
          <div className="academy-hero-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                const blob = new Blob([exportPayload], {
                  type: "application/json",
                });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "yonder-instructor-summary.json";
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Export summary JSON
            </button>
          </div>
        ) : null}
      </header>

      {error ? (
        <div className={forbidden ? "lab-banner" : "message-error models-error"}>
          {error}
          {forbidden ? (
            <p>
              <Link href="/academy">Return to Academy gallery</Link>
            </p>
          ) : null}
        </div>
      ) : null}
      {message ? <p className="academy-feedback">{message}</p> : null}

      {data ? (
        <>
          {data.class_runner ? (
            <section className="academy-panel" aria-label="Class runner">
              <h2>Class runner</h2>
              <p className="muted">{data.class_runner.note}</p>
              <p>
                Recommended mission:{" "}
                <code>{data.class_runner.recommended_mission_id}</code> · Cohort:{" "}
                <code>{data.class_runner.recommended_cohort_id}</code>
              </p>
              <div className="academy-hero-actions">
                {Object.entries(data.class_runner.join_urls).map(([key, href]) => (
                  <Link key={key} className="btn" href={href}>
                    Join — {key}
                  </Link>
                ))}
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => {
                    const text = Object.entries(data.class_runner!.join_urls)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join("\n");
                    void navigator.clipboard.writeText(text);
                    setMessage("Copied class join URLs");
                  }}
                >
                  Copy join URLs
                </button>
              </div>
            </section>
          ) : null}

          <section className="academy-status-cards" aria-label="Instructor summary">
            <article className="academy-status-card">
              <p className="lab-card-kicker">Learners seen</p>
              <h2>{data.principals_seen}</h2>
              <p>{data.note}</p>
            </article>
            {Object.entries(data.challenge_completions).map(([id, count]) => (
              <article key={id} className="academy-status-card">
                <p className="lab-card-kicker">Challenge completions</p>
                <h2>{count}</h2>
                <p>{id}</p>
              </article>
            ))}
          </section>

          <section className="academy-tracks">
            {data.tracks.map((track) => (
              <article key={track.track_id} className="academy-track">
                <div className="academy-track-head">
                  <div>
                    <p className="lab-card-kicker">{track.track_id}</p>
                    <h2>{track.title}</h2>
                    <p>
                      Completed: {track.learners_completed} · In progress:{" "}
                      {track.learners_in_progress} · Seen: {track.learners_seen} ·{" "}
                      {track.lesson_total} lessons
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() =>
                      setExpandedTrack((id) =>
                        id === track.track_id ? null : track.track_id,
                      )
                    }
                  >
                    {expandedTrack === track.track_id ? "Hide" : "Lesson drill-down"}
                  </button>
                </div>
                {expandedTrack === track.track_id ? (
                  <ul className="academy-lesson-list academy-lesson-list-compact">
                    {(track.lessons ?? []).map((lesson) => (
                      <li key={lesson.lesson_id}>
                        <Link href={`/academy/lessons/${lesson.lesson_id}`}>
                          <strong>{lesson.lesson_id}</strong>
                          <em>
                            done {lesson.completed_count} · in progress{" "}
                            {lesson.in_progress_count} · stalled{" "}
                            {lesson.stalled_count}
                            {data.stall_days ? ` (>${data.stall_days}d)` : ""}
                          </em>
                        </Link>
                      </li>
                    ))}
                    {(track.lessons ?? []).length === 0 ? (
                      <li>
                        <span className="muted">
                          No per-lesson rows yet (progress counts only).
                        </span>
                      </li>
                    ) : null}
                  </ul>
                ) : null}
              </article>
            ))}
          </section>

          <section className="academy-sandbox-grid">
            <div className="academy-panel">
              <h2>Reset demo progress</h2>
              <label>
                Principal subject
                <input
                  value={resetSubject}
                  onChange={(e) => setResetSubject(e.target.value)}
                  placeholder="subject from RequestPrincipal"
                />
              </label>
              <button
                type="button"
                className="btn"
                disabled={busy || !resetSubject.trim()}
                onClick={() => void onReset()}
              >
                Reset progress
              </button>
            </div>
            <div className="academy-panel">
              <h2>Corpus quarantine</h2>
              <p className="muted">
                Quarantined presets cannot be used to create Practice desks.
                Current: {data.quarantined_corpora.join(", ") || "(none)"}
              </p>
              <ul className="academy-lesson-list">
                {PRESET_CORPORA.map((id) => {
                  const quarantined = data.quarantined_corpora.includes(id);
                  return (
                    <li key={id}>
                      <strong>{id}</strong>
                      <span
                        className={`academy-status ${
                          quarantined
                            ? "academy-status-in_progress"
                            : "academy-status-completed"
                        }`}
                      >
                        {quarantined ? "quarantined" : "available"}
                      </span>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy}
                        onClick={() =>
                          void onCorpus(id, quarantined ? "release" : "quarantine")
                        }
                      >
                        {quarantined ? "Release" : "Quarantine"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>
        </>
      ) : null}
      <InstitutionPanel />
    </div>
  );
}
