"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  GatewayError,
  getAcademyCurriculum,
  resetAcademyProgress,
  type AcademyCurriculum,
  type AcademyTrack,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";

type TrackTone = "cyan" | "ember" | "mint" | "gold" | "rose";

type TrackGroup = {
  id: string;
  title: string;
  blurb: string;
  tone: TrackTone;
  trackIds: string[];
};

const TRACK_GROUPS: TrackGroup[] = [
  {
    id: "start",
    title: "Start here",
    blurb: "Gateway-only fundamentals and trust literacy — everyone begins here.",
    tone: "cyan",
    trackIds: ["foundations", "trust"],
  },
  {
    id: "roles",
    title: "Role tracks",
    blurb: "Site Ops, Support, Security, Product, and Managers — pick your lane.",
    tone: "ember",
    trackIds: ["site_ops", "support", "security", "pm", "managers"],
  },
  {
    id: "build",
    title: "Scale & build",
    blurb: "Capacity honesty, operator readiness, and builder craft.",
    tone: "mint",
    trackIds: ["capacity", "operator", "builder"],
  },
];

function trackStatusLabel(track: AcademyTrack): {
  label: string;
  kind: "complete" | "progress" | "empty";
} {
  const { completed, total } = track.completion;
  if (total === 0) return { label: "No lessons", kind: "empty" };
  if (completed === total) {
    return { label: "Track complete", kind: "complete" };
  }
  if (completed === 0) return { label: "Not started", kind: "empty" };
  return {
    label: `${completed}/${total} done`,
    kind: "progress",
  };
}

function firstOpenLesson(track: AcademyTrack): string | null {
  const open = track.lessons.find((l) => l.progress?.status !== "completed");
  return (open ?? track.lessons[0])?.id ?? null;
}

const LESSON_PREVIEW = 6;

export function AcademyGallery() {
  const [data, setData] = useState<AcademyCurriculum | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [expandedTracks, setExpandedTracks] = useState<Record<string, boolean>>(
    {},
  );

  async function load() {
    try {
      const body = await getAcademyCurriculum();
      setData(body);
      setError(null);
    } catch (err) {
      if (err instanceof GatewayError) {
        setError(`${err.message} [${err.code}]`);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load Academy");
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await getAcademyCurriculum();
        if (!cancelled) setData(body);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof GatewayError) {
          setError(`${err.message} [${err.code}]`);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load Academy");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onReset() {
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Reset all Academy lesson progress for your principal? This cannot be undone.",
      )
    ) {
      return;
    }
    setResetting(true);
    try {
      await resetAcademyProgress();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setResetting(false);
    }
  }

  const tracksById = new Map((data?.tracks ?? []).map((t) => [t.id, t]));
  const groupedIds = new Set(TRACK_GROUPS.flatMap((g) => g.trackIds));
  const leftover = (data?.tracks ?? []).filter((t) => !groupedIds.has(t.id));

  const completedCount = (data?.tracks ?? []).filter(
    (t) => t.completion.total > 0 && t.completion.completed === t.completion.total,
  ).length;
  const lessonDone = (data?.tracks ?? []).reduce(
    (n, t) => n + t.completion.completed,
    0,
  );
  const lessonTotal = (data?.tracks ?? []).reduce((n, t) => n + t.completion.total, 0);
  const globalContinue =
    (data?.tracks ?? [])
      .flatMap((t) => t.lessons)
      .find((l) => l.progress?.status !== "completed")?.id ?? null;

  return (
    <div className="academy-layout academy-layout-lively">
      <AcademySubnav />
      <header className="academy-hero academy-hero-lively">
        <div className="academy-hero-copy">
          <p className="empty-kicker academy-hero-kicker">Academy</p>
          <h1>Learn the platform</h1>
          <p className="academy-hero-lead">
            Gateway-mediated AI with honest readiness — progress keyed to your
            trusted identity. Pick a track, ship a mission, stay grounded.
          </p>
          <div className="academy-hero-actions">
            <Link
              className="btn"
              href="/academy/missions/siteops-incident-loop"
            >
              Site Ops live desk
            </Link>
            {globalContinue ? (
              <Link className="btn btn-ghost" href={`/academy/lessons/${globalContinue}`}>
                Continue learning
              </Link>
            ) : null}
            <Link
              className="btn btn-ghost"
              href="/academy/sandbox?preset=site-ops-fixture"
            >
              Practice desk
            </Link>
            <Link className="btn btn-ghost" href="/academy/missions">
              Missions
            </Link>
            <Link className="btn btn-ghost" href="/academy/cohorts">
              Cohorts
            </Link>
            <Link className="btn btn-ghost" href="/academy/capacity">
              Capacity
            </Link>
          </div>
        </div>
        <aside className="academy-hero-pulse" aria-label="Your progress snapshot">
          <p className="lab-card-kicker">Your principal</p>
          <strong className="academy-pulse-stat">
            {lessonDone}
            <span>/{lessonTotal || "—"}</span>
          </strong>
          <p>lessons complete</p>
          <p className="academy-pulse-sub">
            {completedCount} track{completedCount === 1 ? "" : "s"} finished · progress
            only — never a vanity score
          </p>
          <div className="academy-hero-more">
            <Link className="btn btn-ghost" href="/academy/workbench">
              Workbench
            </Link>
            <Link className="btn btn-ghost" href="/academy/prompts">
              Prompts
            </Link>
            <Link className="btn btn-ghost" href="/academy/instructor">
              Instructor
            </Link>
            <Link className="btn btn-ghost" href="/lab">
              Lab
            </Link>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={onReset}
              disabled={resetting}
            >
              {resetting ? "Resetting…" : "Reset progress"}
            </button>
          </div>
        </aside>
      </header>

      <section className="academy-group academy-group-cyan" aria-label="Cold fleet demo">
        <header className="academy-group-head">
          <div>
            <h2>Cold-fleet demo path</h2>
            <p>
              One sitting when nothing is loaded: foundations honesty → trust
              fail-closed → Site Ops live desk. EnsureReady + Gateway-only —
              never invent readiness.
            </p>
          </div>
        </header>
        <div className="academy-tracks academy-tracks-grid">
          <article className="academy-track">
            <p className="lab-card-kicker">1 · Foundations</p>
            <h3>Gateway-only basics</h3>
            <p className="academy-track-summary">
              Aliases vs GPUs, streaming chat, honest readiness.
            </p>
            <Link className="btn academy-continue" href="/academy/lessons/foundations-gateway-only">
              Start foundations
            </Link>
          </article>
          <article className="academy-track">
            <p className="lab-card-kicker">2 · Trust</p>
            <h3>Fail closed</h3>
            <p className="academy-track-summary">
              Citations, schemas, and reading errors honestly.
            </p>
            <Link
              className="btn academy-continue"
              href="/academy/lessons/trust-fail-closed-schemas"
            >
              Open trust lesson
            </Link>
          </article>
          <article className="academy-track academy-track-ember">
            <p className="lab-card-kicker">3 · Live desk</p>
            <h3>Site Ops incident loop</h3>
            <p className="academy-track-summary">
              Mission loop on fixtures — same path as the ember desk below.
            </p>
            <Link className="btn academy-continue" href="/academy/missions/siteops-incident-loop">
              Open mission
            </Link>
          </article>
        </div>
      </section>

      <section className="academy-group academy-group-ember" aria-label="Live Ops Desk">
        <header className="academy-group-head">
          <div>
            <h2>Site Ops live desk</h2>
            <p>
              Golden path: SEL triage → fixture retrieve → incident intake → RMA
              packet. Fail closed on unknowns — never invent serials or warranty.
            </p>
          </div>
        </header>
        <div className="academy-tracks academy-tracks-grid">
          <article className="academy-track academy-track-ember">
            <p className="lab-card-kicker">Continuous loop</p>
            <h3>Incident loop mission</h3>
            <p className="academy-track-summary">
              One sitting: Recipes + Practice desk + honesty check for fixture
              lab-host-a / DIMM.
            </p>
            <Link className="btn academy-continue" href="/academy/missions/siteops-incident-loop">
              Open live desk
            </Link>
          </article>
          <article className="academy-track academy-track-ember">
            <p className="lab-card-kicker">Practice</p>
            <h3>site-ops-fixture</h3>
            <p className="academy-track-summary">
              Retrieve SEL / inventory / runbook teaching docs. Parts ledger is
              Site Ops–only.
            </p>
            <Link
              className="btn academy-continue"
              href="/academy/sandbox?preset=site-ops-fixture"
            >
              Open Practice desk
            </Link>
          </article>
          <article className="academy-track academy-track-ember">
            <p className="lab-card-kicker">Governed actions</p>
            <h3>Site Ops Recipes</h3>
            <p className="academy-track-summary">
              sel-triage, dc-incident-structured, rma-packet — readiness-honest.
            </p>
            <Link className="btn academy-continue" href="/recipes">
              Open Recipes
            </Link>
          </article>
        </div>
      </section>

      {error ? <div className="message-error models-error">{error}</div> : null}
      {!data && !error ? (
        <p className="muted academy-skeleton">Loading tracks…</p>
      ) : null}

      {TRACK_GROUPS.map((group) => {
        const tracks = group.trackIds
          .map((id) => tracksById.get(id))
          .filter((t): t is AcademyTrack => Boolean(t));
        if (!tracks.length) return null;
        return (
          <section
            key={group.id}
            className={`academy-group academy-group-${group.tone}`}
            aria-labelledby={`academy-group-${group.id}`}
          >
            <header className="academy-group-head">
              <div>
                <h2 id={`academy-group-${group.id}`}>{group.title}</h2>
                <p>{group.blurb}</p>
              </div>
            </header>
            <div className="academy-tracks academy-tracks-grid">
              {tracks.map((track) => {
                const status = trackStatusLabel(track);
                const continueId = firstOpenLesson(track);
                return (
                  <article
                    key={track.id}
                    className={`academy-track academy-track-${group.tone} academy-track-${status.kind}`}
                  >
                    <div className="academy-track-head">
                      <div>
                        <p className="lab-card-kicker">{track.audience}</p>
                        <h3>{track.title}</h3>
                        <p className="academy-track-summary">{track.summary}</p>
                        <p
                          className={`academy-track-status academy-track-status-${status.kind}`}
                        >
                          {status.label}
                        </p>
                      </div>
                      <div
                        className="academy-progress"
                        aria-label={`${track.completion.percent}% complete`}
                      >
                        <div className="academy-progress-track">
                          <div
                            className="academy-progress-bar"
                            style={{ width: `${track.completion.percent}%` }}
                          />
                        </div>
                        <span>
                          {track.completion.completed}/{track.completion.total}
                        </span>
                      </div>
                    </div>
                    {continueId ? (
                      <Link
                        className="btn academy-continue"
                        href={`/academy/lessons/${continueId}`}
                      >
                        {status.kind === "complete" ? "Review first lesson" : "Continue"}
                      </Link>
                    ) : null}
                    <ol className="academy-lesson-list academy-lesson-list-compact">
                      {(expandedTracks[track.id]
                        ? track.lessons
                        : track.lessons.slice(0, LESSON_PREVIEW)
                      ).map((lesson) => {
                        const lessonStatus = lesson.progress?.status ?? "not_started";
                        return (
                          <li key={lesson.id}>
                            <Link href={`/academy/lessons/${lesson.id}`}>
                              <span
                                className={`academy-status academy-status-${lessonStatus}`}
                              >
                                {lessonStatus.replace("_", " ")}
                              </span>
                              <strong>{lesson.title}</strong>
                              <em>{lesson.duration_minutes}m</em>
                            </Link>
                          </li>
                        );
                      })}
                    </ol>
                    {track.lessons.length > LESSON_PREVIEW ? (
                      <button
                        type="button"
                        className="btn btn-ghost"
                        onClick={() =>
                          setExpandedTracks((prev) => ({
                            ...prev,
                            [track.id]: !prev[track.id],
                          }))
                        }
                      >
                        {expandedTracks[track.id]
                          ? "Show fewer"
                          : `Show all ${track.lessons.length}`}
                      </button>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </section>
        );
      })}

      {leftover.length ? (
        <section className="academy-group academy-group-gold">
          <header className="academy-group-head">
            <div>
              <h2>More tracks</h2>
              <p>Additional curriculum not yet filed into a group.</p>
            </div>
          </header>
          <div className="academy-tracks academy-tracks-grid">
            {leftover.map((track) => {
              const status = trackStatusLabel(track);
              return (
                <article
                  key={track.id}
                  className={`academy-track academy-track-gold academy-track-${status.kind}`}
                >
                  <div className="academy-track-head">
                    <div>
                      <p className="lab-card-kicker">{track.audience}</p>
                      <h3>{track.title}</h3>
                      <p className="academy-track-summary">{track.summary}</p>
                    </div>
                  </div>
                  <ol className="academy-lesson-list academy-lesson-list-compact">
                    {track.lessons.map((lesson) => (
                      <li key={lesson.id}>
                        <Link href={`/academy/lessons/${lesson.id}`}>
                          <strong>{lesson.title}</strong>
                          <em>{lesson.duration_minutes}m</em>
                        </Link>
                      </li>
                    ))}
                  </ol>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}
    </div>
  );
}
