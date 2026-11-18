import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./MessageBubble";

describe("MessageBubble generation metadata", () => {
  it("renders an inline image for succeeded generation jobs", () => {
    const markup = renderToStaticMarkup(
      <MessageBubble
        message={{
          id: "m1",
          role: "assistant",
          content: "Here’s the image.",
          metadata: {
            kind: "generation",
            job_id: "diffusers-req_1",
            artifact_id: "art_1",
            mime_type: "image/png",
            mode: "txt2img",
            status: "succeeded",
            url: "/v1/generation/artifacts/art_1",
          },
        }}
      />,
    );
    expect(markup).toContain("message-media");
    expect(markup).toContain("/v1/generation/artifacts/art_1");
    expect(markup).toContain("Download");
    expect(markup).toContain("/studio?job=diffusers-req_1");
  });

  it("shows generating copy while pending", () => {
    const markup = renderToStaticMarkup(
      <MessageBubble
        message={{
          id: "m2",
          role: "assistant",
          content: "Generating image…",
          pending: true,
          metadata: {
            kind: "generation",
            job_id: "diffusers-req_2",
            mode: "txt2img",
            status: "running",
          },
        }}
      />,
    );
    expect(markup).toContain("Generating image");
    expect(markup).not.toContain("<img");
  });
});
