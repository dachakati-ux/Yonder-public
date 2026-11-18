import { APP_HITS } from "./appCatalog";
import { GLOSSARY_TERMS } from "./glossaryData";
import type { AppHit, GlossaryTerm } from "./types";

export interface TermMatch {
  term: GlossaryTerm;
  score: number;
  reason: "exact" | "alias" | "short";
}

function normalize(q: string): string {
  return q.trim().toLowerCase();
}

/** Rank glossary terms: exact term > alias > substring in short/body. */
export function matchGlossaryTerms(
  query: string,
  terms: GlossaryTerm[] = GLOSSARY_TERMS,
  limit = 8,
): TermMatch[] {
  const q = normalize(query);
  if (!q) return [];

  const scored: TermMatch[] = [];
  for (const term of terms) {
    const termLower = term.term.toLowerCase();
    if (termLower === q || term.id === q) {
      scored.push({ term, score: 300, reason: "exact" });
      continue;
    }
    if (term.aliases.some((a) => a.toLowerCase() === q)) {
      scored.push({ term, score: 250, reason: "alias" });
      continue;
    }
    if (termLower.startsWith(q) || term.id.startsWith(q)) {
      scored.push({ term, score: 200, reason: "exact" });
      continue;
    }
    if (term.aliases.some((a) => a.toLowerCase().includes(q))) {
      scored.push({ term, score: 150, reason: "alias" });
      continue;
    }
    if (term.short.toLowerCase().includes(q) || term.body.toLowerCase().includes(q)) {
      scored.push({ term, score: 100, reason: "short" });
    }
  }

  return scored
    .sort((a, b) => b.score - a.score || a.term.term.localeCompare(b.term.term))
    .slice(0, limit);
}

export function matchAppHits(query: string, limit = 6): AppHit[] {
  const q = normalize(query);
  if (!q) return [];
  return APP_HITS.filter((hit) => {
    const hay = [hit.label, ...hit.keywords].join(" ").toLowerCase();
    return hay.includes(q);
  }).slice(0, limit);
}

/** Build Chat deep-link for the helper AI button. */
export function buildHelperAiHref(query: string, model = "yonder-chat"): string {
  const trimmed = query.trim();
  const prompt = trimmed
    ? `Explain in Yonder terms: ${trimmed}`
    : "Explain Yonder aliases and what READY means.";
  const params = new URLSearchParams();
  params.set("prompt", prompt);
  params.set("model", model);
  params.set("recipe", "helper-ai");
  return `/?${params.toString()}`;
}
