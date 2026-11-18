import { describe, expect, it } from "vitest";

import { extractSsePayloads, parseSsePayload } from "./sse";

describe("SSE parsing", () => {
  it("retains partial lines across network chunks", () => {
    const first = extractSsePayloads('data: {"choices":[{"delta":{"content":"hel');
    expect(first.payloads).toEqual([]);

    const second = extractSsePayloads(`${first.remainder}lo"}}]}\r\n`);
    expect(second.payloads).toHaveLength(1);
    expect(parseSsePayload(second.payloads[0])).toEqual({
      kind: "token",
      token: "hello",
    });
  });

  it("recognizes completion and structured errors", () => {
    expect(parseSsePayload("[DONE]")).toEqual({ kind: "done" });
    expect(
      parseSsePayload(
        JSON.stringify({
          error: { code: "INFERENCE_TIMEOUT", message: "timed out", request_id: "req_1" },
        }),
      ),
    ).toEqual({
      kind: "error",
      error: { code: "INFERENCE_TIMEOUT", message: "timed out", request_id: "req_1" },
    });
  });

  it("parses yonder.context control events", () => {
    const parsed = parseSsePayload(
      JSON.stringify({
        object: "yonder.context",
        condensed: true,
        meter: {
          context_length: 65536,
          context_length_availability: "available",
          prompt_chars: 100,
          estimate_prompt_tokens: 25,
          pressure_ratio: 0.2,
          pressure_availability: "estimate",
          prompt_tokens: null,
          soft_ratio: 0.7,
          should_condense: false,
        },
        messages: [{ id: "msg_1", role: "user", content: "hi" }],
      }),
    );
    expect(parsed.kind).toBe("context");
    if (parsed.kind === "context") {
      expect(parsed.event.condensed).toBe(true);
      expect(parsed.event.messages).toHaveLength(1);
    }
  });
});
