import { describe, expect, it } from "vitest";
import { trustChipsFromText } from "./trustChips";
import { todaysRecipePicks } from "@/features/recipes/dailyRecipes";
import { RECIPES } from "@/features/recipes/catalog";

describe("trustChipsFromText", () => {
  it("detects not_reported honesty", () => {
    const chips = trustChipsFromText(
      "warranty_end is not_reported; leave unknown empty.",
    );
    expect(chips.some((c) => c.id === "not_reported")).toBe(true);
    expect(chips.some((c) => c.id === "unknown")).toBe(true);
  });

  it("warns when citations are not detected on long text", () => {
    const chips = trustChipsFromText(
      "x".repeat(100) + " everything looks fine always.",
    );
    expect(chips.some((c) => c.id === "citations_missing")).toBe(true);
  });

  it("flags coder overclaim language", () => {
    const chips = trustChipsFromText("I ran the tests and applied the patch.");
    expect(chips.some((c) => c.id === "coder_overclaim")).toBe(true);
  });

  it("detects proposal language", () => {
    const chips = trustChipsFromText("Here is a PROPOSED ONLY unified diff.");
    expect(chips.some((c) => c.id === "coder_proposal")).toBe(true);
  });
});

describe("todaysRecipePicks", () => {
  it("returns up to five recipes deterministically", () => {
    const a = todaysRecipePicks(RECIPES, 5);
    const b = todaysRecipePicks(RECIPES, 5);
    expect(a).toHaveLength(5);
    expect(a.map((r) => r.id)).toEqual(b.map((r) => r.id));
  });
});
