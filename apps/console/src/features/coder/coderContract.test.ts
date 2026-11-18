import { describe, expect, it } from "vitest";
import {
  CODER_ALIAS,
  CODER_SYSTEM_PROMPT,
  buildCoderChatHref,
  coderSessionTrustChips,
} from "./coderContract";

describe("coderContract", () => {
  it("binds the stable coder alias", () => {
    expect(CODER_ALIAS).toBe("yonder-coder");
    expect(CODER_SYSTEM_PROMPT).toMatch(/read-only/i);
    expect(CODER_SYSTEM_PROMPT).toMatch(/PROPOSED ONLY/);
  });

  it("emits honest alias chips", () => {
    const ready = coderSessionTrustChips({
      aliasAvailability: "ready",
      hasWorkspace: false,
    });
    expect(ready.some((c) => c.id === "read_only")).toBe(true);
    expect(ready.some((c) => c.id === "no_workspace")).toBe(true);
    expect(ready.some((c) => c.id === "alias_ready")).toBe(true);

    const missing = coderSessionTrustChips({
      aliasAvailability: "not_reported",
      hasWorkspace: true,
    });
    expect(missing.some((c) => c.id === "workspace_read")).toBe(true);
    expect(missing.some((c) => c.id === "alias_not_reported")).toBe(true);
  });

  it("builds a Chat deep-link with desk=coder", () => {
    const href = buildCoderChatHref({ prompt: "Review this function" });
    expect(href.startsWith("/?")).toBe(true);
    const q = new URLSearchParams(href.slice(2));
    expect(q.get("desk")).toBe("coder");
    expect(q.get("model")).toBe("yonder-coder");
    expect(q.get("system")).toContain("read-only");
    expect(q.get("prompt")).toBe("Review this function");
  });
});
