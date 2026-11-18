import { describe, expect, it } from "vitest";
import { EVAL_PROMPTS } from "./evalPack";

describe("eval pack", () => {
  it("has substring expectations for every case", () => {
    expect(EVAL_PROMPTS.length).toBeGreaterThanOrEqual(4);
    for (const item of EVAL_PROMPTS) {
      expect(item.expectSubstrings.length).toBeGreaterThan(0);
      expect(item.prompt.length).toBeGreaterThan(10);
    }
  });
});
