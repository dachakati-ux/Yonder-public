"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { fetchRoutes } from "@/lib/admin";
import { GATEWAY_URL } from "@/lib/brand";
import { GatewayError, listModels } from "@/lib/gateway";
import { saveToWorkbench } from "@/features/academy/workbenchSave";
import { lessonsForRecipe } from "@/features/academy/academyLessonsByRecipe";
import {
  RECIPES,
  buildChatHref,
  buildCurl,
  resolveRecipeReadiness,
  type Recipe,
  type RecipeReadiness,
} from "./catalog";
import { todaysRecipePicks } from "./dailyRecipes";

const READY_STATES = new Set(["enabled", "ready", "AVAILABLE", "available"]);

function readinessLabel(status: RecipeReadiness): string {
  switch (status) {
    case "ready":
      return "ready";
    case "not_ready":
      return "not_ready";
    case "not_supported":
      return "not_supported";
    default:
      return "unknown";
  }
}

export function RecipesGallery() {
  const [readyAliases, setReadyAliases] = useState<Set<string>>(new Set());
  const [listedModels, setListedModels] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [routes, models] = await Promise.all([
          fetchRoutes().catch(() => null),
          listModels().catch(() => []),
        ]);
        if (cancelled) return;
        const ready = new Set<string>();
        if (routes) {
          const depReady = new Set(
            (routes.deployments || [])
              .filter((d) => ["ready", "loaded"].includes(String(d.status)))
              .map((d) => d.id),
          );
          for (const alias of routes.aliases || []) {
            const state = String(alias.state || "").toLowerCase();
            if (
              depReady.has(alias.deployment_id) ||
              READY_STATES.has(state) ||
              state === "enabled"
            ) {
              if (depReady.has(alias.deployment_id) || state === "enabled") {
                if (depReady.has(alias.deployment_id)) {
                  ready.add(alias.alias);
                }
              }
            }
          }
        }
        setReadyAliases(ready);
        setListedModels(new Set(models.map((m) => m.id)));
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof GatewayError
            ? `${err.message} [${err.code}]`
            : err instanceof Error
              ? err.message
              : "Failed to load recipe readiness",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const cards = useMemo(
    () =>
      RECIPES.map((recipe) => ({
        recipe,
        status: resolveRecipeReadiness(recipe, {
          readyAliases,
          listedModels,
        }),
      })),
    [readyAliases, listedModels],
  );

  const daily = useMemo(() => todaysRecipePicks(RECIPES, 5), []);

  async function copyCurl(recipe: Recipe) {
    const text = buildCurl(recipe, GATEWAY_URL);
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(recipe.id);
      window.setTimeout(() => setCopiedId(null), 2000);
    } catch {
      setError("Clipboard unavailable — select the curl from the card.");
    }
  }

  async function saveStarter(recipe: Recipe) {
    try {
      const body = [
        `Recipe: ${recipe.id}`,
        `Alias: ${recipe.alias}`,
        "",
        "SYSTEM:",
        recipe.systemPrompt || "(none)",
        "",
        "USER SAMPLE:",
        recipe.samplePrompt,
      ].join("\n");
      await saveToWorkbench({
        title: `Recipe starter — ${recipe.title}`,
        body,
        tags: ["recipe", recipe.id],
        source: "recipe",
      });
      setSavedId(recipe.id);
      window.setTimeout(() => setSavedId(null), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workbench save failed");
    }
  }

  function renderCard(recipe: Recipe, status: RecipeReadiness) {
    const disabled = status === "not_ready" && recipe.modality === "code";
    const chatHref = buildChatHref(recipe);
    return (
      <article key={recipe.id} className="lab-card">
        <div className="lab-card-kicker">
          Wave {recipe.wave} · {recipe.modality} · {readinessLabel(status)}
        </div>
        <h2>{recipe.title}</h2>
        <p>{recipe.problem}</p>
        <p className="lab-card-meta">
          Alias <code>{recipe.alias}</code>
        </p>
        {lessonsForRecipe(recipe.id).length ? (
          <p className="lab-card-meta">
            Academy lessons:{" "}
            {lessonsForRecipe(recipe.id).map((id, i) => (
              <span key={id}>
                {i > 0 ? ", " : ""}
                <Link href={`/academy/lessons/${id}`}>{id}</Link>
              </span>
            ))}
          </p>
        ) : null}
        <div className="lab-card-actions">
          {disabled ? (
            <button type="button" className="btn btn-primary" disabled>
              not_ready
            </button>
          ) : (
            <Link className="btn btn-primary" href={chatHref}>
              Run in Chat
            </Link>
          )}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void saveStarter(recipe)}
          >
            {savedId === recipe.id ? "Saved" : "Save to Workbench"}
          </button>
          {recipe.labHref ? (
            <Link className="btn btn-ghost" href={recipe.labHref}>
              Open Lab
            </Link>
          ) : null}
          {recipe.playgroundHref ? (
            <Link className="btn btn-ghost" href={recipe.playgroundHref}>
              Open Playground
            </Link>
          ) : null}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void copyCurl(recipe)}
          >
            {copiedId === recipe.id ? "Copied" : "Copy curl"}
          </button>
        </div>
      </article>
    );
  }

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">Ops recipes</p>
          <h1>Daily action catalog</h1>
          <p>
            Don&apos;t invent a prompt — run a reviewed Recipe. Every run goes
            through Yonder Gateway. Cards show honest readiness — never a fake
            success.
          </p>
          <div className="academy-hero-actions">
            <Link className="btn" href="/academy/missions/siteops-incident-loop">
              Site Ops live desk
            </Link>
            <Link className="btn btn-ghost" href="/academy/workbench">
              Workbench
            </Link>
          </div>
        </div>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section aria-label="Today's picks">
        <h2 className="lab-card-kicker">Today&apos;s picks</h2>
        <p className="muted">
          Deterministic daily rotation of the catalog — not a popularity score.
        </p>
        <div className="lab-grid">
          {daily.map((recipe) => {
            const status = resolveRecipeReadiness(recipe, {
              readyAliases,
              listedModels,
            });
            return renderCard(recipe, status);
          })}
        </div>
      </section>

      <section aria-label="All recipes">
        <h2 className="lab-card-kicker">Full catalog</h2>
        <div className="lab-grid">
          {cards.map(({ recipe, status }) => renderCard(recipe, status))}
        </div>
      </section>
    </div>
  );
}
