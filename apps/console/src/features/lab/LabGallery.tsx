"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { GatewayError, listLabAbilities } from "@/lib/gateway";
import type { LabAbilityCard } from "@/types";
import { LAB_ABILITY_META } from "./catalog";

export function LabGallery() {
  const [abilities, setAbilities] = useState<LabAbilityCard[]>([]);
  const [note, setNote] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await listLabAbilities();
        if (cancelled) return;
        setAbilities(body.abilities);
        setNote(body.note);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof GatewayError) {
          setError(`${err.message} [${err.code}]`);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load Lab");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="lab-layout">
      <header className="models-header">
        <div>
          <p className="empty-kicker">Training Lab</p>
          <h1>Learn local AI by doing</h1>
          <p>
            Tool use, RAG, structured output, guided modes, and AI generation —
            each demo runs through Yonder Gateway. Prefer live aliases when READY;
            otherwise demos fall back to labeled training mocks.
          </p>
        </div>
      </header>

      {note ? <div className="lab-banner">{note}</div> : null}
      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="lab-grid">
        {(abilities.length
          ? abilities
          : (Object.keys(LAB_ABILITY_META) as Array<keyof typeof LAB_ABILITY_META>).map(
              (id) => ({
                id,
                title: LAB_ABILITY_META[id].title,
                summary: LAB_ABILITY_META[id].promise,
                wave: 1,
              }),
            )
        ).map((ability) => {
          const meta = LAB_ABILITY_META[ability.id];
          return (
            <article key={ability.id} className="lab-card">
              <div className="lab-card-kicker">Wave {ability.wave}</div>
              <h2>{meta?.title ?? ability.title}</h2>
              <p>{meta?.promise ?? ability.summary}</p>
              <Link className="btn btn-primary" href={meta?.href ?? `/lab/${ability.id}`}>
                Open
              </Link>
            </article>
          );
        })}
        <article className="lab-card">
          <div className="lab-card-kicker">Wave 2</div>
          <h2>Embeddings playground</h2>
          <p>Compare texts with cosine similarity via /v1/embeddings.</p>
          <Link className="btn btn-primary" href="/lab/embeddings">
            Open
          </Link>
        </article>
        <article className="lab-card">
          <div className="lab-card-kicker">Wave 2</div>
          <h2>Rerank sandbox</h2>
          <p>Order candidate docs for an ops query via /v1/rerank.</p>
          <Link className="btn btn-primary" href="/lab/rerank">
            Open
          </Link>
        </article>
        <article className="lab-card">
          <div className="lab-card-kicker">Wave 2</div>
          <h2>Side-by-side compare</h2>
          <p>Same prompt across two chat aliases with client latency.</p>
          <Link className="btn btn-primary" href="/lab/compare">
            Open
          </Link>
        </article>
        <article className="lab-card">
          <div className="lab-card-kicker">Wave 2</div>
          <h2>Light eval pack</h2>
          <p>Fixed substring checks against a live chat alias.</p>
          <Link className="btn btn-primary" href="/lab/eval">
            Open
          </Link>
        </article>
      </section>
    </div>
  );
}
