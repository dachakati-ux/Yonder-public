import { afterEach, describe, expect, it, vi } from "vitest";

import { GatewayError, streamChatCompletion } from "./gateway";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Gateway client", () => {
  it("preserves structured gateway error context", () => {
    const error = new GatewayError(503, {
      error: { code: "MODEL_UNAVAILABLE", message: "offline", request_id: "req_2" },
    });
    expect(error.status).toBe(503);
    expect(error.code).toBe("MODEL_UNAVAILABLE");
    expect(error.requestId).toBe("req_2");
  });

  it("propagates request cancellation to fetch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
      ),
    );
    const controller = new AbortController();
    const request = streamChatCompletion({
      model: "yonder-chat",
      messages: [{ role: "user", content: "stop" }],
      signal: controller.signal,
      onToken: () => undefined,
    });
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });
});
