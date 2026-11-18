"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  GatewayError,
  getChallengeBoard,
  listCohorts,
  submitChallenge,
  type AcademyChallenge,
  type AcademyCohort,
} from "@/lib/gateway";
import { AcademySubnav } from "./AcademySubnav";

function windowBanner(startsAt: string, endsAt: string): {
  label: string;
  kind: "active" | "soon" | "ended" | "upcoming";
} {
  const now = Date.now();
  const start = Date.parse(startsAt);
  const end = Date.parse(endsAt);
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return { label: `${startsAt} → ${endsAt}`, kind: "active" };
  }
  if (now > end) return { label: "Ended", kind: "ended" };
  if (now < start) return { label: "Upcoming", kind: "upcoming" };
  const daysLeft = (end - now) / (1000 * 60 * 60 * 24);
  if (daysLeft <= 7) return { label: "Ending soon", kind: "soon" };
  return { label: "Active", kind: "active" };
}

export function CohortsWorkspace() {
  const [cohorts, setCohorts] = useState<AcademyCohort[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [met, setMet] = useState<string[]>([]);
  const [missed, setMissed] = useState<string[]>([]);
  const [board, setBoard] = useState<
    Array<{ principal_subject: string; criteria_met: string[]; submitted_at: string }>
  >([]);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    try {
      const body = await listCohorts();
      setCohorts(body.cohorts);
      setError(null);
      const first = body.cohorts[0]?.challenges[0];
      if (!activeId && first) {
        setActiveId(first.id);
        void onShowBoard(first.id);
      }
    } catch (err) {
      setError(
        err instanceof GatewayError
          ? `${err.message} [${err.code}]`
          : err instanceof Error
            ? err.message
            : "Failed to load cohorts",
      );
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const challenges = cohorts.flatMap((c) => c.challenges);
  const active: AcademyChallenge | undefined = challenges.find((c) => c.id === activeId);

  async function onSubmit() {
    if (!active) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await submitChallenge(active.id, answer);
      setMet(result.criteria_met ?? []);
      setMissed(result.criteria_missed ?? []);
      setFeedback(
        `${result.passed ? "Criteria met" : "Not yet"} — ${result.explanation}. ${result.note}`,
      );
      const boardBody = await getChallengeBoard(active.id);
      setBoard(boardBody.completions);
      await load();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function onShowBoard(challengeId: string) {
    try {
      const body = await getChallengeBoard(challengeId);
      setBoard(body.completions);
      setFeedback(body.note);
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Board failed");
    }
  }

  return (
    <div className="academy-layout academy-layout-lively">
      <AcademySubnav />
      <header className="academy-hero academy-hero-lively">
        <div className="academy-hero-copy">
          <p className="empty-kicker academy-hero-kicker">Cohorts</p>
          <h1>Challenges</h1>
          <p className="academy-hero-lead">
            Time-boxed practice with honest rubrics: criteria met or missed —
            never a vanity score.
          </p>
        </div>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="academy-group academy-group-mint">
        <div className="academy-tracks academy-tracks-grid">
          {cohorts.map((cohort) => {
            const banner = windowBanner(cohort.starts_at, cohort.ends_at);
            return (
              <article key={cohort.id} className="academy-track academy-track-mint">
                <div className="academy-track-head">
                  <div>
                    <p className="lab-card-kicker">
                      {banner.label} · {cohort.starts_at} → {cohort.ends_at}
                    </p>
                    <h3>{cohort.title}</h3>
                    <p className="academy-track-summary">{cohort.summary}</p>
                  </div>
                </div>
                <ul className="academy-lesson-list academy-lesson-list-compact">
                  {cohort.challenges.map((challenge) => {
                    const passed = challenge.submission?.passed;
                    return (
                      <li key={challenge.id}>
                        <button
                          type="button"
                          className="academy-challenge-pick"
                          onClick={() => {
                            setActiveId(challenge.id);
                            setAnswer("");
                            setFeedback(null);
                            setMet([]);
                            setMissed([]);
                            void onShowBoard(challenge.id);
                          }}
                        >
                          <span
                            className={`academy-status ${
                              passed
                                ? "academy-status-completed"
                                : "academy-status-not_started"
                            }`}
                          >
                            {passed ? "criteria met" : "open"}
                          </span>
                          <strong>{challenge.title}</strong>
                          <em>{challenge.track_hint || "challenge"}</em>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </article>
            );
          })}
        </div>
      </section>

      {active ? (
        <section className="academy-sandbox-grid">
          <div className="academy-panel">
            <h2>{active.title}</h2>
            <p>{active.summary}</p>
            <p className="muted">{active.completion_prompt}</p>
            <ul className="academy-objectives">
              {active.criteria.map((c) => (
                <li key={c.id}>{c.label}</li>
              ))}
            </ul>
            {active.sandbox_preset ? (
              <p>
                Practice desk preset:{" "}
                <Link
                  href={`/academy/sandbox?preset=${encodeURIComponent(active.sandbox_preset)}`}
                >
                  {active.sandbox_preset}
                </Link>
              </p>
            ) : null}
            {active.mission_id ? (
              <p>
                Mission:{" "}
                <Link href={`/academy/missions/${active.mission_id}`}>
                  {active.mission_id}
                </Link>
              </p>
            ) : null}
            <label>
              Your answer
              <textarea
                rows={8}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Paste grounded evidence…"
              />
            </label>
            <div className="academy-actions">
              <button
                type="button"
                className="btn"
                disabled={submitting || !answer.trim()}
                onClick={() => void onSubmit()}
              >
                {submitting ? "Checking…" : "Submit for criteria check"}
              </button>
            </div>
            {met.length || missed.length ? (
              <div className="academy-criteria-chips" aria-label="Criteria result">
                {met.map((id) => (
                  <span key={`met-${id}`} className="academy-chip academy-chip-met">
                    met: {id}
                  </span>
                ))}
                {missed.map((id) => (
                  <span
                    key={`miss-${id}`}
                    className="academy-chip academy-chip-missed"
                  >
                    missed: {id}
                  </span>
                ))}
              </div>
            ) : null}
            {feedback ? <p className="academy-feedback">{feedback}</p> : null}
          </div>
          <div className="academy-panel">
            <h2>Completions board</h2>
            <p className="muted">
              Principals who met criteria. No ranks or invented scores.
            </p>
            {board.length === 0 ? (
              <p className="muted">No completions yet.</p>
            ) : (
              <ul className="academy-lesson-list">
                {board.map((row) => (
                  <li key={`${row.principal_subject}-${row.submitted_at}`}>
                    <span className="academy-status academy-status-completed">met</span>
                    <strong>{row.principal_subject}</strong>
                    <em>{row.submitted_at}</em>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
