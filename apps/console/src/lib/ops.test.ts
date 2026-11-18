import { describe, expect, it } from "vitest";

import { formatMetric } from "./ops";

describe("hardware metric formatting", () => {
  it("renders explicit unavailable states", () => {
    expect(
      formatMetric({
        availability: "permission_unavailable",
        message: "access denied",
      }),
    ).toBe("permission_unavailable — access denied");
  });

  it("renders reported values with units", () => {
    expect(formatMetric({ availability: "available", value: 42, unit: "percent" })).toBe(
      "42 percent",
    );
  });
});
