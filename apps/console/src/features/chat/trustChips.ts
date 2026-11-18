/** Honesty / trust chips derived from assistant text — never invent readiness. */

export type TrustChip = {
  id: string;
  label: string;
  kind: "ok" | "warn" | "info";
};

export function trustChipsFromText(content: string): TrustChip[] {
  const text = content || "";
  const lower = text.toLowerCase();
  const chips: TrustChip[] = [];

  if (/\bnot_reported\b/i.test(text) || /\bnot reported\b/i.test(lower)) {
    chips.push({
      id: "not_reported",
      label: "honesty: not_reported used",
      kind: "ok",
    });
  }
  if (/\bunknown\b/i.test(lower) || /\bfail[- ]?closed\b/i.test(lower)) {
    chips.push({
      id: "unknown",
      label: "fail-closed / unknown acknowledged",
      kind: "ok",
    });
  }
  if (
    /\bcitation\b/i.test(lower) ||
    /\bcites?\b/i.test(lower) ||
    /\baccording to\b/i.test(lower) ||
    /\bfrom the (doc|corpus|runbook)\b/i.test(lower)
  ) {
    chips.push({
      id: "citations",
      label: "citations: language detected",
      kind: "info",
    });
  } else if (text.trim().length > 80) {
    chips.push({
      id: "citations_missing",
      label: "citations: not detected",
      kind: "warn",
    });
  }
  if (/\bready\b/i.test(lower) && !/\bnot[_ ]?ready\b/i.test(lower)) {
    // Soft signal only — do not claim alias is READY.
    chips.push({
      id: "ready_word",
      label: "mentions ready — verify against Capacity",
      kind: "warn",
    });
  }
  // Coder overclaim signals — assistant must not pretend to have filesystem/shell.
  if (
    /\bi (ran|executed|opened|cloned|applied|wrote to|saved to)\b/i.test(lower) ||
    /\b(applied the patch|ran the tests|opened the repo)\b/i.test(lower)
  ) {
    chips.push({
      id: "coder_overclaim",
      label: "possible execution claim — verify read_only",
      kind: "warn",
    });
  }
  if (/\bproposed only\b/i.test(lower) || /\bproposal(?:\s+only)?\b/i.test(lower)) {
    chips.push({
      id: "coder_proposal",
      label: "proposal language",
      kind: "ok",
    });
  }
  return chips;
}
