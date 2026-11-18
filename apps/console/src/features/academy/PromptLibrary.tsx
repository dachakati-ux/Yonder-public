"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  GatewayError,
  listPromptPatterns,
  type PromptPattern,
} from "@/lib/gateway";
import { recipeById, buildChatHref } from "@/features/recipes/catalog";
import { AcademySubnav } from "./AcademySubnav";

export function PromptLibrary() {
  const [patterns, setPatterns] = useState<PromptPattern[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [trackFilter, setTrackFilter] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await listPromptPatterns();
        if (cancelled) return;
        setPatterns(body.patterns);
        setNote(body.note);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof GatewayError
            ? `${err.message} [${err.code}]`
            : err instanceof Error
              ? err.message
              : "Failed to load prompts",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const tracks = useMemo(
    () => Array.from(new Set(patterns.map((p) => p.track))).sort(),
    [patterns],
  );

  const visible = useMemo(
    () =>
      trackFilter === "all"
        ? patterns
        : patterns.filter((p) => p.track === trackFilter),
    [patterns, trackFilter],
  );

  async function copyPattern(pattern: PromptPattern) {
    const text = `SYSTEM:\n${pattern.system}\n\nUSER:\n${pattern.user_template}`;
    await navigator.clipboard.writeText(text);
    setCopied(pattern.id);
  }

  function tryInChatHref(pattern: PromptPattern): string {
    const recipe = pattern.links.recipe_id
      ? recipeById(pattern.links.recipe_id)
      : undefined;
    if (recipe) return buildChatHref(recipe);
    const params = new URLSearchParams();
    params.set("system", pattern.system);
    params.set("prompt", pattern.user_template);
    params.set("model", "yonder-chat");
    return `/?${params.toString()}`;
  }

  return (
    <div className="academy-layout academy-layout-lively">
      <AcademySubnav />
      <header className="academy-hero academy-hero-lively">
        <div className="academy-hero-copy">
          <p className="empty-kicker academy-hero-kicker">Prompt library</p>
          <h1>Governed patterns</h1>
          <p className="academy-hero-lead">
            Reviewed starters owned by Academy tracks — not a Slack junk drawer.
            Promote proven patterns to Recipes via operators.
          </p>
          <label className="academy-preset-pick">
            Track
            <select
              value={trackFilter}
              onChange={(e) => setTrackFilter(e.target.value)}
            >
              <option value="all">All tracks</option>
              {tracks.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      {note ? <div className="lab-banner">{note}</div> : null}
      {error ? <div className="message-error models-error">{error}</div> : null}
      <section className="academy-group academy-group-cyan">
        <div className="academy-tracks academy-tracks-grid">
          {visible.map((pattern) => (
            <article key={pattern.id} className="academy-track academy-track-cyan">
              <p className="lab-card-kicker">
                {pattern.track} · {pattern.status}
              </p>
              <h3>{pattern.title}</h3>
              <pre className="academy-prompt-block">{pattern.system}</pre>
              <pre className="academy-prompt-block">{pattern.user_template}</pre>
              <div className="academy-hero-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={() => void copyPattern(pattern)}
                >
                  {copied === pattern.id ? "Copied" : "Copy"}
                </button>
                <Link className="btn" href={tryInChatHref(pattern)}>
                  Try in Chat
                </Link>
                {pattern.links.lesson_id ? (
                  <Link
                    className="btn btn-ghost"
                    href={`/academy/lessons/${pattern.links.lesson_id}`}
                  >
                    Lesson
                  </Link>
                ) : null}
                {pattern.links.recipe_id ? (
                  <Link
                    className="btn btn-ghost"
                    href={
                      recipeById(pattern.links.recipe_id)
                        ? buildChatHref(recipeById(pattern.links.recipe_id)!)
                        : `/?recipe=${encodeURIComponent(pattern.links.recipe_id)}`
                    }
                  >
                    Recipe
                  </Link>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
