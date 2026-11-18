import { describe, expect, it } from "vitest";
import { RECIPES, recipeById, buildChatHref } from "@/features/recipes/catalog";
import { buildTryItHref, buildMissionStepHref } from "./tryItHref";
import { lessonsForRecipe } from "./academyLessonsByRecipe";

describe("academy recipes", () => {
  it("includes Site Ops recipes", () => {
    expect(recipeById("sel-triage")).toBeTruthy();
    expect(recipeById("dc-incident-structured")).toBeTruthy();
    expect(recipeById("rma-packet")).toBeTruthy();
    expect(recipeById("inventory-qa")).toBeTruthy();
    expect(RECIPES.some((r) => r.id === "sel-triage")).toBe(true);
  });
});

describe("buildTryItHref", () => {
  it("adds sandbox preset when href is bare", () => {
    expect(
      buildTryItHref({
        href: "/academy/sandbox",
        sandbox_preset: "site-ops-fixture",
      }),
    ).toBe("/academy/sandbox?preset=site-ops-fixture");
  });

  it("deep-links recipes via chat href", () => {
    const recipe = recipeById("ticket-triage")!;
    const href = buildTryItHref(
      { href: "/recipes", recipe_id: "ticket-triage" },
      { recipeChatHref: buildChatHref(recipe), fromLesson: "support-triage-basics" },
    );
    expect(href.startsWith("/?")).toBe(true);
    expect(href).toContain("recipe=ticket-triage");
    expect(href).toContain("fromLesson=support-triage-basics");
  });

  it("appends fromMission on lab ability steps", () => {
    expect(
      buildMissionStepHref(
        { href: "/lab/tools", ability: "tools" },
        { fromMission: "ticket-tool-loop" },
      ),
    ).toBe("/lab/tools?fromMission=ticket-tool-loop");
  });
});

describe("academyLessonsByRecipe", () => {
  it("maps sel-triage to Site Ops lesson", () => {
    expect(lessonsForRecipe("sel-triage")).toContain("site-ops-sel-triage");
  });
});
