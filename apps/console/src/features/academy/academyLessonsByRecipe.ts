/** Reverse index: recipe id → Academy lesson ids that deep-link that recipe. */

export const ACADEMY_LESSONS_BY_RECIPE: Record<string, string[]> = {
  "ticket-triage": ["support-triage-basics"],
  "sel-triage": ["site-ops-sel-triage"],
  "dc-incident-structured": [
    "site-ops-issue-event-schema",
    "support-structured-intake",
  ],
  "rma-packet": ["site-ops-rma-packet"],
  "inventory-qa": ["site-ops-inventory-qa"],
};

export function lessonsForRecipe(recipeId: string): string[] {
  return ACADEMY_LESSONS_BY_RECIPE[recipeId] ?? [];
}
