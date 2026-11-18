/** Deterministic “today’s picks” from the Recipes catalog (no backend). */

import type { Recipe } from "@/features/recipes/catalog";

export function todaysRecipePicks(recipes: Recipe[], count = 5): Recipe[] {
  if (recipes.length === 0) return [];
  const day = Math.floor(Date.now() / 86_400_000);
  const start = ((day % recipes.length) + recipes.length) % recipes.length;
  const picks: Recipe[] = [];
  for (let i = 0; i < Math.min(count, recipes.length); i += 1) {
    picks.push(recipes[(start + i) % recipes.length]!);
  }
  return picks;
}
