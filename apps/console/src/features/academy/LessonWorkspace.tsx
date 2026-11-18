"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  GatewayError,
  getAcademyCurriculum,
  postLessonEvent,
  type AcademyLesson,
  type AcademyTrack,
} from "@/lib/gateway";
import { findTerm, useGlossaryTerms } from "@/features/help/useGlossary";
import { recipeById, buildChatHref } from "@/features/recipes/catalog";
import { AcademySubnav } from "./AcademySubnav";
import { buildTryItHref } from "./tryItHref";

function explainTermHref(term: string, short: string): string {
  const prompt =
    `Explain the Yonder term "${term}" for an Academy learner in plain language. ` +
    `Authoritative short definition: ${short}. Do not invent hardware state or readiness.`;
  const params = new URLSearchParams({
    prompt,
    model: "yonder-chat",
  });
  return `/?${params.toString()}`;
}

export function LessonWorkspace({ lessonId }: { lessonId: string }) {
  const [lesson, setLesson] = useState<AcademyLesson | null>(null);
  const [track, setTrack] = useState<AcademyTrack | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [evalAnswer, setEvalAnswer] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const { terms } = useGlossaryTerms();

  const glossaryLinks = useMemo(() => {
    if (!lesson) return [];
    return (lesson.glossary_term_ids ?? [])
      .map((id) => findTerm(terms, id))
      .filter((t): t is NonNullable<typeof t> => Boolean(t));
  }, [lesson, terms]);

  useEffect(() => {
    let cancelled = false;
    setSelected("");
    setEvalAnswer("");
    setFeedback(null);
    setChaptersOpen(false);
    (async () => {
      try {
        const body = await getAcademyCurriculum();
        if (cancelled) return;
        const parent =
          body.tracks.find((t) => t.lessons.some((l) => l.id === lessonId)) ?? null;
        const found = parent?.lessons.find((l) => l.id === lessonId) ?? null;
        setTrack(parent);
        setLesson(found);
        if (found) {
          await postLessonEvent(lessonId, { event: "start" });
        } else {
          setError("Lesson not found.");
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof GatewayError) {
          setError(`${err.message} [${err.code}]`);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load lesson");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lessonId]);

  async function onTryIt() {
    if (!lesson?.try_it) return;
    setBusy(true);
    try {
      await postLessonEvent(lessonId, { event: "try_it" });
      const recipe = lesson.try_it.recipe_id
        ? recipeById(lesson.try_it.recipe_id)
        : undefined;
      const href = buildTryItHref(lesson.try_it, {
        fromLesson: lessonId,
        recipeChatHref: recipe ? buildChatHref(recipe) : null,
      });
      window.location.href = href;
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "try_it failed");
      setBusy(false);
    }
  }

  async function onCheck() {
    if (!lesson) return;
    setBusy(true);
    setFeedback(null);
    try {
      const result = await postLessonEvent(lessonId, {
        event: "check",
        selected_option_id: lesson.check.kind === "quiz" ? selected : undefined,
        answer: lesson.check.kind === "eval_case" ? evalAnswer : undefined,
      });
      setFeedback(
        result.check_correct
          ? `Correct. ${result.explanation ?? ""}`
          : `Not yet. ${result.explanation ?? ""}`,
      );
      // Optimistic chapter status without waiting on full curriculum refetch.
      setLesson((prev) =>
        prev
          ? {
              ...prev,
              progress: {
                status: result.completed
                  ? "completed"
                  : prev.progress?.status === "completed"
                    ? "completed"
                    : "in_progress",
                check_passed: result.check_passed,
                try_it_recorded:
                  prev.progress?.try_it_recorded ?? result.try_it_recorded,
                check_attempts: (prev.progress?.check_attempts ?? 0) + 1,
                completed_at: result.completed
                  ? new Date().toISOString()
                  : prev.progress?.completed_at,
              },
            }
          : prev,
      );
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Check failed");
    } finally {
      setBusy(false);
    }
  }

  if (!lesson && !error) {
    return (
      <div className="academy-layout">
        <AcademySubnav />
        <p className="muted academy-skeleton">Loading lesson…</p>
      </div>
    );
  }

  if (!lesson) {
    return (
      <div className="academy-layout">
        <AcademySubnav />
        <div className="message-error models-error">{error}</div>
        <Link href="/academy">Back to Academy</Link>
      </div>
    );
  }

  const nextId = lesson.next_lesson_id;
  const chapters = track?.lessons ?? [];
  const chapterIndex = chapters.findIndex((l) => l.id === lessonId);
  const prevId =
    chapterIndex > 0 ? chapters[chapterIndex - 1]?.id ?? null : null;

  return (
    <div className="academy-layout">
      <AcademySubnav />
      <div className="academy-lesson-shell">
        <button
          type="button"
          className="btn btn-ghost academy-chapters-toggle"
          onClick={() => setChaptersOpen((o) => !o)}
        >
          {chaptersOpen ? "Hide chapters" : "Chapters"}
        </button>
        <aside
          className={
            chaptersOpen
              ? "academy-chapters academy-chapters-open"
              : "academy-chapters"
          }
          aria-label="Track chapters"
        >
          <div className="academy-chapters-head">
            <Link href="/academy" className="academy-chapters-back">
              ← Academy
            </Link>
            <p className="lab-card-kicker">{track?.audience ?? lesson.track}</p>
            <h2>{track?.title ?? lesson.track}</h2>
            <p className="academy-chapters-meta">
              {chapterIndex >= 0 ? chapterIndex + 1 : "—"} / {chapters.length || "—"}
            </p>
          </div>
          <ol className="academy-chapters-list">
            {chapters.map((chapter, idx) => {
              const status = chapter.progress?.status ?? "not_started";
              const active = chapter.id === lessonId;
              const liveStatus =
                chapter.id === lessonId
                  ? (lesson.progress?.status ?? status)
                  : status;
              return (
                <li key={chapter.id}>
                  <Link
                    href={`/academy/lessons/${chapter.id}`}
                    className={
                      active
                        ? "academy-chapter academy-chapter-active"
                        : `academy-chapter academy-chapter-${liveStatus}`
                    }
                    aria-current={active ? "page" : undefined}
                    onClick={() => setChaptersOpen(false)}
                  >
                    <span className="academy-chapter-num">{idx + 1}</span>
                    <span className="academy-chapter-body">
                      <strong>{chapter.title}</strong>
                      <em>
                        {liveStatus.replace("_", " ")} · {chapter.duration_minutes}m
                      </em>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ol>
        </aside>

        <div className="academy-lesson-stage">
        <article className="academy-lesson-card">
          <nav className="academy-crumb">
            <Link href="/academy">Academy</Link>
            <span>/</span>
            <span>{track?.title ?? lesson.track}</span>
            <span>/</span>
            <span>{lesson.title}</span>
          </nav>

          <header className="academy-lesson-hero">
            <p className="empty-kicker academy-hero-kicker">
              {lesson.duration_minutes} min
            </p>
            <h1>{lesson.title}</h1>
            <p className="academy-lesson-summary">{lesson.summary}</p>
            <ul className="academy-objectives">
              {lesson.objectives.map((obj) => (
                <li key={obj}>{obj}</li>
              ))}
            </ul>
          </header>

          {error ? <div className="message-error models-error">{error}</div> : null}

          <section className="academy-sections">
            {lesson.sections.map((section) => (
              <article key={section.title}>
                <h2>{section.title}</h2>
                <p>{section.body}</p>
              </article>
            ))}
          </section>

          {glossaryLinks.length ? (
            <section className="academy-glossary" aria-label="Glossary terms">
              <h2>Terms</h2>
              <ul>
                {glossaryLinks.map((term) => (
                  <li key={term.id}>
                    <Link href={`/help#${term.id}`}>{term.term}</Link>
                    <span>{term.short}</span>
                    <Link
                      className="btn btn-ghost academy-explain"
                      href={explainTermHref(term.term, term.short)}
                    >
                      Explain in Chat
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="academy-actions">
            {lesson.try_it ? (
              <button type="button" className="btn" onClick={onTryIt} disabled={busy}>
                Try it — {lesson.try_it.label}
              </button>
            ) : null}
            <div className="academy-check">
              <h2>Check</h2>
              <p>{lesson.check.prompt}</p>
              {lesson.check.kind === "quiz" && lesson.check.options ? (
                <div className="academy-options">
                  {lesson.check.options.map((opt) => (
                    <label key={opt.id}>
                      <input
                        type="radio"
                        name="quiz"
                        value={opt.id}
                        checked={selected === opt.id}
                        onChange={() => setSelected(opt.id)}
                      />
                      {opt.label}
                    </label>
                  ))}
                </div>
              ) : (
                <textarea
                  value={evalAnswer}
                  onChange={(e) => setEvalAnswer(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      void onCheck();
                    }
                  }}
                  rows={5}
                  placeholder="Write your answer — include the evidence words the check expects…"
                />
              )}
              <button
                type="button"
                className="btn"
                onClick={onCheck}
                disabled={
                  busy ||
                  (lesson.check.kind === "quiz" && !selected) ||
                  (lesson.check.kind === "eval_case" && !evalAnswer.trim())
                }
              >
                Submit check
              </button>
              {feedback ? <p className="academy-feedback">{feedback}</p> : null}
              {lesson.progress?.status === "completed" ? (
                <p className="academy-feedback academy-ok">Lesson completed.</p>
              ) : null}
            </div>
            <div className="academy-lesson-nav">
              {prevId ? (
                <Link className="btn btn-ghost" href={`/academy/lessons/${prevId}`}>
                  Previous lesson
                </Link>
              ) : null}
              {nextId ? (
                <Link className="btn" href={`/academy/lessons/${nextId}`}>
                  Next lesson
                </Link>
              ) : (
                <Link className="btn" href="/academy">
                  Back to tracks
                </Link>
              )}
              <Link className="btn btn-ghost" href="/academy">
                All tracks
              </Link>
            </div>
          </section>
        </article>
      </div>
      </div>
    </div>
  );
}
