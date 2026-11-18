import { describe, expect, it } from "vitest";

import type { ChatComposerMode } from "@/types";

function generationAliasFor(mode: ChatComposerMode): string | null {
  if (mode === "image") return "yonder-image";
  if (mode === "video") return "yonder-video";
  return null;
}

describe("composer generation aliases", () => {
  it("maps image/video modes to studio aliases", () => {
    expect(generationAliasFor("chat")).toBeNull();
    expect(generationAliasFor("image")).toBe("yonder-image");
    expect(generationAliasFor("video")).toBe("yonder-video");
  });
});
