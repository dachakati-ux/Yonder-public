/** Thin eval prompt pack — substring checks only, no invented scores. */

export interface EvalCase {
  id: string;
  title: string;
  prompt: string;
  system?: string;
  expectSubstrings: string[];
}

export const EVAL_PROMPTS: EvalCase[] = [
  {
    id: "gateway-only",
    title: "Gateway-only rule",
    prompt:
      "In one short sentence, should Yonder consoles call GPU workers directly? Answer yes or no and why.",
    expectSubstrings: ["no"],
  },
  {
    id: "honest-metrics",
    title: "Honest metrics",
    prompt:
      "Name one explicit state Yonder uses when a GPU metric is unavailable.",
    expectSubstrings: ["not_"],
  },
  {
    id: "no-auth",
    title: "No local auth",
    prompt:
      "Does Yonder authenticate corporate users itself? Answer yes or no briefly.",
    expectSubstrings: ["no"],
  },
  {
    id: "alias-not-path",
    title: "Stable aliases",
    prompt:
      "Should clients address models by filesystem path or by stable alias? Reply with one word: alias or path.",
    expectSubstrings: ["alias"],
  },
];
