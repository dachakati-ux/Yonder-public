import { describe, expect, it } from "vitest";
import {
  RECIPES,
  buildChatHref,
  buildCurl,
  resolveRecipeReadiness,
} from "./catalog";
import { parseChatStarter } from "./chatStarter";

describe("recipes catalog", () => {
  it("ships ops and Site Ops recipes", () => {
    expect(RECIPES).toHaveLength(10);
    expect(RECIPES.map((r) => r.id)).toEqual([
      "ticket-triage",
      "policy-qa",
      "intake-grade",
      "doc-search-rerank",
      "code-assist",
      "meeting-actions",
      "sel-triage",
      "dc-incident-structured",
      "rma-packet",
      "inventory-qa",
    ]);
  });

  it("builds chat deep-link with prompt and model", () => {
    const href = buildChatHref(RECIPES[0]);
    expect(href.startsWith("/?")).toBe(true);
    const qs = new URLSearchParams(href.slice(2));
    expect(qs.get("recipe")).toBe("ticket-triage");
    expect(qs.get("model")).toBe("yonder-chat");
    expect(qs.get("prompt")).toContain("VPN");
  });

  it("builds curl against gateway path", () => {
    const curl = buildCurl(RECIPES[0], "http://127.0.0.1:8080");
    expect(curl).toContain("POST 'http://127.0.0.1:8080/v1/chat/completions'");
    expect(curl).toContain("yonder-chat");
  });

  it("resolves readiness without inventing ready", () => {
    expect(
      resolveRecipeReadiness(RECIPES[4], {
        readyAliases: new Set(),
        listedModels: new Set(["yonder-chat"]),
      }),
    ).toBe("not_ready");
    expect(
      resolveRecipeReadiness(RECIPES[0], {
        readyAliases: new Set(["yonder-chat"]),
        listedModels: new Set(),
      }),
    ).toBe("ready");
  });
});

describe("parseChatStarter", () => {
  it("parses recipe query params", () => {
    const starter = parseChatStarter(
      new URLSearchParams(
        "recipe=ticket-triage&model=yonder-chat&prompt=hello&system=sys",
      ),
    );
    expect(starter).toEqual({
      recipeId: "ticket-triage",
      model: "yonder-chat",
      prompt: "hello",
      system: "sys",
    });
  });

  it("returns nulls when absent", () => {
    expect(parseChatStarter(new URLSearchParams())).toEqual({
      recipeId: null,
      prompt: null,
      model: null,
      system: null,
    });
  });
});
