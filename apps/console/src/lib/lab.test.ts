import { afterEach, describe, expect, it, vi } from "vitest";

import { runLab } from "./gateway";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Lab client", () => {
  it("posts ability runs to /v1/lab/run", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          ability: "tools",
          request_id: "req_lab",
          mode: "training_scenario",
          provider: "mock",
          steps: [],
          answer: "ok",
          citations: [],
          structured: null,
          teaching: { how_it_works: [], how_to_build: [] },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await runLab({
      ability: "tools",
      input: "Weather in Seattle",
    });

    expect(result.answer).toBe("ok");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/lab/run");
    expect(JSON.parse(String(init.body))).toEqual({
      ability: "tools",
      input: "Weather in Seattle",
    });
  });
});
