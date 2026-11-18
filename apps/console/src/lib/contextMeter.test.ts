import { describe, expect, it } from "vitest";

import { formatContextMeterLabel, type ContextMeter } from "./contextMeter";

function meter(overrides: Partial<ContextMeter>): ContextMeter {
  return {
    context_length: 65536,
    context_length_availability: "available",
    prompt_chars: 1000,
    estimate_prompt_tokens: 250,
    pressure_ratio: 0.42,
    pressure_availability: "estimate",
    prompt_tokens: null,
    soft_ratio: 0.7,
    should_condense: false,
    ...overrides,
  };
}

describe("formatContextMeterLabel", () => {
  it("labels estimate pressure with a tilde", () => {
    expect(formatContextMeterLabel(meter({}))).toBe("Context ~42% · estimate");
  });

  it("labels provider pressure without inventing estimates", () => {
    expect(
      formatContextMeterLabel(
        meter({
          pressure_availability: "provider",
          prompt_tokens: 12000,
          pressure_ratio: 0.183,
        }),
      ),
    ).toBe("Context 18% · provider");
  });

  it("reports not_reported when the denominator is missing", () => {
    expect(
      formatContextMeterLabel(
        meter({
          context_length: null,
          context_length_availability: "not_reported",
          pressure_ratio: null,
          pressure_availability: "not_reported",
        }),
      ),
    ).toBe("Context not_reported");
  });

  it("reports not_reported for a null meter", () => {
    expect(formatContextMeterLabel(null)).toBe("Context not_reported");
  });
});
