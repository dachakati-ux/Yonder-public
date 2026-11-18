/**
 * Voice command router — wake word is a leading "Yonder" on the transcript.
 * Mid-sentence mentions do not command. v1 allowlist is navigation (+ chat open).
 */

export type YonderNavTarget =
  | "chat"
  | "academy"
  | "studio"
  | "recipes"
  | "models"
  | "operations"
  | "lab"
  | "help"
  | "hardware"
  | "workbench"
  | "capacity"
  | "missions"
  | "prompts"
  | "cohorts"
  | "sandbox"
  | "instructor"
  | "embeddings"
  | "rerank"
  | "coder";

export type YonderCommandResult =
  | { kind: "none"; draft: string }
  | { kind: "unknown"; draft: string; message: string }
  | { kind: "navigate"; href: string; target: YonderNavTarget; remainder: string }
  | { kind: "open_chat"; remainder: string };

const WAKE = /^yonder[\s,.:;!?-]*/i;

const NAV_PHRASES: Array<{ target: YonderNavTarget; href: string; patterns: RegExp[] }> = [
  {
    target: "coder",
    href: "/coder",
    patterns: [/^open\s+coder\b/i, /^go\s+to\s+coder\b/i, /^show\s+coder\b/i],
  },
  {
    target: "academy",
    href: "/academy",
    patterns: [/^open\s+academy\b/i, /^go\s+to\s+academy\b/i, /^show\s+academy\b/i],
  },
  {
    target: "studio",
    href: "/studio",
    patterns: [/^open\s+studio\b/i, /^go\s+to\s+studio\b/i, /^show\s+studio\b/i],
  },
  {
    target: "recipes",
    href: "/recipes",
    patterns: [/^open\s+recipes\b/i, /^go\s+to\s+recipes\b/i, /^show\s+recipes\b/i],
  },
  {
    target: "models",
    href: "/models",
    patterns: [/^open\s+models\b/i, /^go\s+to\s+models\b/i, /^show\s+models\b/i],
  },
  {
    target: "operations",
    href: "/operations",
    patterns: [
      /^open\s+operations\b/i,
      /^go\s+to\s+operations\b/i,
      /^show\s+operations\b/i,
    ],
  },
  {
    target: "lab",
    href: "/lab",
    patterns: [/^open\s+lab\b/i, /^go\s+to\s+lab\b/i, /^show\s+lab\b/i],
  },
  {
    target: "help",
    href: "/help",
    patterns: [/^open\s+help\b/i, /^go\s+to\s+help\b/i, /^show\s+help\b/i],
  },
  {
    target: "hardware",
    href: "/hardware",
    patterns: [/^open\s+hardware\b/i, /^go\s+to\s+hardware\b/i, /^show\s+hardware\b/i],
  },
  {
    target: "workbench",
    href: "/academy/workbench",
    patterns: [
      /^open\s+workbench\b/i,
      /^go\s+to\s+workbench\b/i,
      /^show\s+workbench\b/i,
    ],
  },
  {
    target: "capacity",
    href: "/academy/capacity",
    patterns: [/^open\s+capacity\b/i, /^go\s+to\s+capacity\b/i, /^show\s+capacity\b/i],
  },
  {
    target: "missions",
    href: "/academy/missions",
    patterns: [/^open\s+missions\b/i, /^go\s+to\s+missions\b/i, /^show\s+missions\b/i],
  },
  {
    target: "prompts",
    href: "/academy/prompts",
    patterns: [/^open\s+prompts\b/i, /^go\s+to\s+prompts\b/i, /^show\s+prompts\b/i],
  },
  {
    target: "cohorts",
    href: "/academy/cohorts",
    patterns: [/^open\s+cohorts\b/i, /^go\s+to\s+cohorts\b/i, /^show\s+cohorts\b/i],
  },
  {
    target: "sandbox",
    href: "/academy/sandbox",
    patterns: [
      /^open\s+sandbox\b/i,
      /^go\s+to\s+sandbox\b/i,
      /^show\s+sandbox\b/i,
      /^open\s+practice\b/i,
      /^open\s+practice\s+desk\b/i,
    ],
  },
  {
    target: "instructor",
    href: "/academy/instructor",
    patterns: [
      /^open\s+instructor\b/i,
      /^go\s+to\s+instructor\b/i,
      /^show\s+instructor\b/i,
    ],
  },
  {
    target: "embeddings",
    href: "/lab/embeddings",
    patterns: [
      /^open\s+embeddings\b/i,
      /^go\s+to\s+embeddings\b/i,
      /^show\s+embeddings\b/i,
    ],
  },
  {
    target: "rerank",
    href: "/lab/rerank",
    patterns: [/^open\s+rerank\b/i, /^go\s+to\s+rerank\b/i, /^show\s+rerank\b/i],
  },
];

const CHAT_OPEN = /^(let'?s\s+chat|lets\s+chat|open\s+chat|start\s+chat|start\s+a\s+chat)\b/i;

export function startsWithYonderWake(transcript: string): boolean {
  return WAKE.test(transcript.trim());
}

export function stripYonderWake(transcript: string): string {
  return transcript.trim().replace(WAKE, "").trim();
}

/** Route a transcript. Non-wake speech returns kind "none" with the full text as draft. */
export function routeYonderCommand(transcript: string): YonderCommandResult {
  const raw = transcript.trim();
  if (!raw) {
    return { kind: "none", draft: "" };
  }
  if (!startsWithYonderWake(raw)) {
    return { kind: "none", draft: raw };
  }
  const remainder = stripYonderWake(raw);
  if (!remainder) {
    return {
      kind: "unknown",
      draft: "",
      message: "No matching command",
    };
  }
  if (CHAT_OPEN.test(remainder)) {
    return {
      kind: "open_chat",
      remainder: remainder.replace(CHAT_OPEN, "").trim(),
    };
  }
  for (const entry of NAV_PHRASES) {
    for (const pattern of entry.patterns) {
      if (pattern.test(remainder)) {
        return {
          kind: "navigate",
          href: entry.href,
          target: entry.target,
          remainder: remainder.replace(pattern, "").trim(),
        };
      }
    }
  }
  return {
    kind: "unknown",
    draft: remainder,
    message: "No matching command",
  };
}
