"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  GatewayError,
  getMission,
  postMissionEvent,
  type AcademyMission,
} from "@/lib/gateway";
import { recipeById, buildChatHref } from "@/features/recipes/catalog";
import { AcademySubnav } from "./AcademySubnav";
import { buildMissionStepHref } from "./tryItHref";

export function MissionWorkspace({ missionId }: { missionId: string }) {
  const [mission, setMission] = useState<AcademyMission | null>(null);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);
  const [status, setStatus] = useState("not_started");
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await getMission(missionId);
        if (cancelled) return;
        setMission(body.mission);
        setCompletedSteps(body.progress?.completed_steps ?? []);
        setStatus(body.progress?.status ?? "not_started");
        await postMissionEvent(missionId, { event: "start" });
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof GatewayError
            ? `${err.message} [${err.code}]`
            : err instanceof Error
              ? err.message
              : "Failed to load mission",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  function stepHref(step: AcademyMission["steps"][number]): string | null {
    if (!step.href && !step.sandbox_preset && !step.recipe_id && !step.ability) {
      return null;
    }
    const recipe = step.recipe_id ? recipeById(step.recipe_id) : undefined;
    return buildMissionStepHref(
      {
        kind: step.kind,
        href: step.href,
        sandbox_preset: step.sandbox_preset,
        recipe_id: step.recipe_id,
        ability: step.ability,
      },
      {
        fromMission: missionId,
        recipeChatHref: recipe ? buildChatHref(recipe) : null,
      },
    );
  }

  async function markStep(
    stepId: string,
    href: string | null,
    mode: "here" | "tab" | "mark",
  ) {
    setBusy(true);
    try {
      const result = await postMissionEvent(missionId, {
        event: "step",
        step_id: stepId,
      });
      setCompletedSteps(result.completed_steps);
      setStatus(result.status);
      if (href && mode === "here") {
        window.location.href = href;
      } else if (href && mode === "tab") {
        window.open(href, "_blank", "noopener,noreferrer");
      }
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Step failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCheck() {
    setBusy(true);
    setFeedback(null);
    try {
      const result = await postMissionEvent(missionId, {
        event: "check",
        answer,
      });
      setStatus(result.status);
      setFeedback(
        result.check_correct
          ? `Passed. ${result.explanation ?? ""}`
          : `Not yet. ${result.explanation ?? ""}`,
      );
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Check failed");
    } finally {
      setBusy(false);
    }
  }

  if (!mission && !error) {
    return (
      <div className="academy-layout">
        <AcademySubnav />
        <p className="muted academy-skeleton">Loading mission…</p>
      </div>
    );
  }
  if (!mission) {
    return (
      <div className="academy-layout">
        <AcademySubnav />
        <div className="message-error models-error">{error}</div>
        <Link href="/academy/missions">Back</Link>
      </div>
    );
  }

  return (
    <div className="academy-layout academy-mission-shell">
      <AcademySubnav />
      <nav className="academy-crumb">
        <Link href="/academy">Academy</Link>
        <span>/</span>
        <Link href="/academy/missions">Missions</Link>
        <span>/</span>
        <span>{mission.track}</span>
      </nav>
      <header className="academy-lesson-hero academy-workspace-hero">
        <p className="empty-kicker academy-hero-kicker">
          {mission.duration_minutes} min · {status}
        </p>
        <h1>{mission.title}</h1>
        <p className="academy-lesson-summary">{mission.summary}</p>
        <ul className="academy-objectives">
          {mission.honesty.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      </header>

      <div className="academy-mission-layout">
        <aside className="academy-mission-rail" aria-label="Mission steps">
          <ol>
            {mission.steps.map((step, index) => {
              const done = completedSteps.includes(step.id);
              return (
                <li key={step.id}>
                  <a
                    href={`#step-${step.id}`}
                    className={done ? "academy-mission-step-done" : undefined}
                  >
                    <span>{index + 1}</span>
                    <strong>{step.title}</strong>
                    <em>{done ? "done" : step.kind}</em>
                  </a>
                </li>
              );
            })}
          </ol>
        </aside>

        <ol className="academy-mission-steps">
          {mission.steps.map((step, index) => {
            const done = completedSteps.includes(step.id);
            const href = stepHref(step);
            return (
              <li
                key={step.id}
                id={`step-${step.id}`}
                className={done ? "done" : undefined}
              >
                <div>
                  <p className="lab-card-kicker">
                    Step {index + 1} · {step.kind}
                    {done ? " · done" : ""}
                  </p>
                  <h2>{step.title}</h2>
                  <p>{step.instruction}</p>
                </div>
                <div className="academy-hero-actions">
                  {href ? (
                    <>
                      <button
                        type="button"
                        className="btn"
                        disabled={busy}
                        onClick={() => void markStep(step.id, href, "here")}
                      >
                        Do step here
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={busy}
                        onClick={() => void markStep(step.id, href, "tab")}
                      >
                        Open in new tab
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn"
                      disabled={busy}
                      onClick={() => void markStep(step.id, null, "mark")}
                    >
                      Mark done
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      <section className="academy-check">
        <h2>Mission check</h2>
        <p>{mission.completion_check.prompt}</p>
        <textarea
          rows={5}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          placeholder="Write your evidence summary…"
        />
        <button
          type="button"
          className="btn"
          disabled={busy || !answer.trim()}
          onClick={onCheck}
        >
          Submit mission check
        </button>
        {feedback ? <p className="academy-feedback">{feedback}</p> : null}
      </section>
    </div>
  );
}
