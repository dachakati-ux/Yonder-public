/**
 * Server-render smoke checks for the operator workspaces.
 *
 * Effects do not run during static rendering, so these assertions cover the
 * pre-data shells: the states an operator sees before any worker heartbeat has
 * been read. They exist to catch render-time regressions in the compact
 * layouts without requiring a browser environment.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { HardwareWorkspace } from "./hardware/HardwareWorkspace";
import { ModelsWorkspace } from "./models/ModelsWorkspace";
import { OperationsWorkspace } from "./operations/OperationsWorkspace";

describe("HardwareWorkspace shell", () => {
  it("reports an empty fleet instead of inventing nodes", () => {
    const markup = renderToStaticMarkup(<HardwareWorkspace />);
    expect(markup).toContain("No workers registered");
    expect(markup).toContain("stat-strip");
    expect(markup).not.toContain("node-card");
  });
});

describe("OperationsWorkspace shell", () => {
  it("waits for the gateway before rendering panels", () => {
    const markup = renderToStaticMarkup(<OperationsWorkspace />);
    expect(markup).toContain("Connecting to gateway");
  });
});

describe("ModelsWorkspace shell", () => {
  it("blocks placement until inventory and a definition are known", () => {
    const markup = renderToStaticMarkup(<ModelsWorkspace />);
    expect(markup).toContain("Fleet occupancy");
    expect(markup).toContain("No hardware inventory yet");
    expect(markup).toContain(
      "Model and context are chosen on the active host",
    );
  });

  it("states that GPU capacity is not reported rather than showing zero", () => {
    const markup = renderToStaticMarkup(<ModelsWorkspace />);
    expect(markup).toContain("not_reported");
    expect(markup).toContain("No hardware inventory yet");
  });
});
