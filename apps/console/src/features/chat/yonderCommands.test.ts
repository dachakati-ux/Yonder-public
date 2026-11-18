import { describe, expect, it } from "vitest";
import { routeYonderCommand, startsWithYonderWake } from "./yonderCommands";

describe("yonderCommands", () => {
  it("treats ordinary speech as draft without commanding", () => {
    expect(startsWithYonderWake("tell me about yonder aliases")).toBe(false);
    expect(routeYonderCommand("tell me about yonder aliases")).toEqual({
      kind: "none",
      draft: "tell me about yonder aliases",
    });
  });

  it("requires a leading wake word", () => {
    expect(routeYonderCommand("Yonder open academy")).toEqual({
      kind: "navigate",
      href: "/academy",
      target: "academy",
      remainder: "",
    });
    expect(routeYonderCommand("please Yonder open academy").kind).toBe("none");
  });

  it("opens chat for let’s-chat synonyms without auto-creating", () => {
    for (const phrase of [
      "Yonder let's chat",
      "yonder lets chat",
      "Yonder, open chat",
      "Yonder start a chat",
    ]) {
      const result = routeYonderCommand(phrase);
      expect(result.kind).toBe("open_chat");
    }
  });

  it("returns unknown for unmatched wake commands and keeps remainder as draft", () => {
    expect(routeYonderCommand("Yonder summon dragons")).toEqual({
      kind: "unknown",
      draft: "summon dragons",
      message: "No matching command",
    });
  });

  it("navigates recipes and studio", () => {
    const recipes = routeYonderCommand("Yonder open recipes");
    expect(recipes).toMatchObject({ kind: "navigate", href: "/recipes" });
    const studio = routeYonderCommand("Yonder go to studio");
    expect(studio).toMatchObject({ kind: "navigate", target: "studio" });
  });

  it("navigates academy capacity and practice desk", () => {
    expect(routeYonderCommand("Yonder open capacity")).toMatchObject({
      kind: "navigate",
      href: "/academy/capacity",
    });
    expect(routeYonderCommand("Yonder open practice desk")).toMatchObject({
      kind: "navigate",
      href: "/academy/sandbox",
    });
  });
});
