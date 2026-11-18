"use client";

import { useEffect, useState } from "react";
import { fetchGlossary } from "@/lib/gateway";
import { GLOSSARY_TERMS } from "./glossaryData";
import type { GlossaryTerm } from "./types";

function normalizeTerms(raw: GlossaryTerm[]): GlossaryTerm[] {
  return raw.filter((t) => t.id && t.term);
}

/**
 * Prefer Gateway `GET /v1/help/glossary` (YAML). Fall back to the bundled
 * TypeScript mirror when Gateway is unreachable — never invent terms.
 */
export function useGlossaryTerms(): {
  terms: GlossaryTerm[];
  source: "gateway" | "bundled";
} {
  const [terms, setTerms] = useState<GlossaryTerm[]>(GLOSSARY_TERMS);
  const [source, setSource] = useState<"gateway" | "bundled">("bundled");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await fetchGlossary();
        if (cancelled) return;
        const next = normalizeTerms(body.terms as GlossaryTerm[]);
        if (next.length) {
          setTerms(next);
          setSource("gateway");
        }
      } catch {
        if (cancelled) return;
        // Keep bundled mirror; do not invent content.
        setTerms(GLOSSARY_TERMS);
        setSource("bundled");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { terms, source };
}

export function findTerm(
  terms: GlossaryTerm[],
  id: string,
): GlossaryTerm | undefined {
  return terms.find((t) => t.id === id);
}
