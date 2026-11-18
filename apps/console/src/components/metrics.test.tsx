import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Gauge, MetricRow, StatTile, TonePill } from "./metrics";

describe("Gauge", () => {
  it("exposes an accessible meter for reported usage", () => {
    const markup = renderToStaticMarkup(
      <Gauge label="VRAM" percent={62.5} detail="10.0 GB / 16.0 GB" />,
    );
    expect(markup).toContain('role="meter"');
    expect(markup).toContain('aria-valuenow="63"');
    expect(markup).toContain("10.0 GB / 16.0 GB");
    expect(markup).toContain('data-tone="ok"');
  });

  it("draws no bar when the metric was not reported", () => {
    const markup = renderToStaticMarkup(
      <Gauge label="VRAM" percent={null} detail="not_reported" />,
    );
    expect(markup).not.toContain('role="meter"');
    expect(markup).toContain("gauge-empty");
  });

  it("escalates tone as usage approaches saturation", () => {
    expect(
      renderToStaticMarkup(<Gauge label="Queue" percent={75} detail="15/20" />),
    ).toContain('data-tone="warn"');
    expect(
      renderToStaticMarkup(<Gauge label="Queue" percent={95} detail="19/20" />),
    ).toContain('data-tone="danger"');
  });
});

describe("MetricRow", () => {
  it("keeps unavailable metrics explicit and styled", () => {
    const markup = renderToStaticMarkup(
      <MetricRow label="CUDA" metric={{ availability: "not_supported" }} />,
    );
    expect(markup).toContain("metric-unavailable");
    expect(markup).toContain("not_supported");
  });

  it("renders byte metrics human-readably", () => {
    const markup = renderToStaticMarkup(
      <MetricRow
        label="Memory total"
        metric={{ availability: "available", value: 64 * 1024 ** 3 }}
        format="bytes"
      />,
    );
    expect(markup).toContain("64.0 GB");
    expect(markup).not.toContain("metric-unavailable");
  });

  it("renders duration metrics as coarse units", () => {
    const markup = renderToStaticMarkup(
      <MetricRow
        label="Uptime"
        metric={{ availability: "available", value: 97200 }}
        format="duration"
      />,
    );
    expect(markup).toContain("1d 3h");
  });
});

describe("tone carriers", () => {
  it("passes tone through to data attributes", () => {
    const markup = renderToStaticMarkup(
      <div>
        <StatTile label="Nodes" value="2" hint="1 connected" tone="warn" />
        <TonePill tone="danger">disconnected</TonePill>
      </div>,
    );
    expect(markup).toContain('data-tone="warn"');
    expect(markup).toContain('data-tone="danger"');
    expect(markup).toContain("1 connected");
  });
});
