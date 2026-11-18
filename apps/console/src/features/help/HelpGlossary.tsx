"use client";

import Link from "next/link";
import { useEffect } from "react";
import { findTerm, useGlossaryTerms } from "./useGlossary";

export function HelpGlossary() {
  const { terms, source } = useGlossaryTerms();

  useEffect(() => {
    const hash = typeof window !== "undefined" ? window.location.hash.slice(1) : "";
    if (!hash) return;
    const el = document.getElementById(hash);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [terms]);

  return (
    <main className="help-page">
      <p className="lab-card-kicker">Help</p>
      <h1>Glossary</h1>
      <p className="help-page-intro">
        Authored platform terms for operators and trainees. Use the topbar helper
        search to find these quickly, or send a question to Chat with the AI
        control. Definitions are curated content — not model output and not live
        hardware state.
        {source === "gateway"
          ? " Loaded from Gateway (config/help/glossary.yaml)."
          : " Showing bundled mirror (Gateway glossary not reachable)."}
      </p>
      {terms.map((term) => (
        <article key={term.id} id={term.id} className="help-term">
          <h2>{term.term}</h2>
          {term.aliases.length ? (
            <p className="help-term-aliases">
              Also: {term.aliases.join(", ")}
            </p>
          ) : null}
          <p className="help-term-short">{term.short}</p>
          <p className="help-term-body">{term.body}</p>
          {term.related.length ? (
            <p className="helper-related">
              Related:{" "}
              {term.related.map((id, index) => {
                const rel = findTerm(terms, id);
                if (!rel) return null;
                return (
                  <span key={id}>
                    {index > 0 ? ", " : null}
                    <Link className="helper-related-link" href={`#${id}`}>
                      {rel.term}
                    </Link>
                  </span>
                );
              })}
            </p>
          ) : null}
          {term.links.length ? (
            <div className="lab-card-actions">
              {term.links.map((link) => (
                <Link key={link.href} className="btn btn-ghost" href={link.href}>
                  {link.label}
                </Link>
              ))}
            </div>
          ) : null}
        </article>
      ))}
    </main>
  );
}
