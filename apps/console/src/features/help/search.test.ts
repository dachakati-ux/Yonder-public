import { describe, expect, it } from "vitest";
import { GLOSSARY_TERMS } from "./glossaryData";
import {
  buildHelperAiHref,
  matchAppHits,
  matchGlossaryTerms,
} from "./search";

describe("matchGlossaryTerms", () => {
  it("ranks exact READY above short substring matches", () => {
    const matches = matchGlossaryTerms("READY");
    expect(matches[0]?.term.id).toBe("ready");
    expect(matches[0]?.reason).toBe("exact");
  });

  it("matches aliases ahead of short-body hits", () => {
    const matches = matchGlossaryTerms("placement_limits");
    expect(matches[0]?.term.id).toBe("placement");
    expect(matches[0]?.reason).toBe("alias");
  });

  it("returns empty for blank query", () => {
    expect(matchGlossaryTerms("   ")).toEqual([]);
  });

  it("seeds core ops terms", () => {
    const ids = new Set(GLOSSARY_TERMS.map((t) => t.id));
    for (const id of [
      "alias",
      "ready",
      "placement",
      "gateway-only",
      "request-principal",
      "not-reported",
      "gauntlet",
      "recipes",
      "lab-live",
      "worker",
    ]) {
      expect(ids.has(id)).toBe(true);
    }
  });
});

describe("matchAppHits", () => {
  it("finds glossary and recipes routes", () => {
    const hits = matchAppHits("glossary");
    expect(hits.some((h) => h.href === "/help")).toBe(true);
    const recipes = matchAppHits("recipes");
    expect(recipes.some((h) => h.href === "/recipes")).toBe(true);
  });
});

describe("buildHelperAiHref", () => {
  it("prefills Chat with explain prefix and model", () => {
    const href = buildHelperAiHref("what is an alias?");
    expect(href.startsWith("/?")).toBe(true);
    const qs = new URLSearchParams(href.slice(2));
    expect(qs.get("model")).toBe("yonder-chat");
    expect(qs.get("recipe")).toBe("helper-ai");
    expect(qs.get("prompt")).toBe("Explain in Yonder terms: what is an alias?");
  });

  it("uses a default prompt when query is empty", () => {
    const qs = new URLSearchParams(buildHelperAiHref("").slice(2));
    expect(qs.get("prompt")).toContain("READY");
  });
});
