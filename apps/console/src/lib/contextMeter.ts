/**
 * Honest labels for chat context pressure.
 *
 * Never present a char÷N heuristic as provider token usage.
 */

export type ContextPressureAvailability =
  | "estimate"
  | "provider"
  | "not_reported";

export type ContextLengthAvailability = "available" | "not_reported";

export type ContextMeter = {
  context_length: number | null;
  context_length_availability: ContextLengthAvailability;
  prompt_chars: number;
  estimate_prompt_tokens: number | null;
  pressure_ratio: number | null;
  pressure_availability: ContextPressureAvailability;
  prompt_tokens: number | null;
  soft_ratio: number;
  should_condense: boolean;
};

export type ContextEvent = {
  condensed: boolean;
  meter: ContextMeter;
  messages: Array<{ id: string; role: string; content: string }>;
};

export function formatContextMeterLabel(meter: ContextMeter | null): string {
  if (!meter) {
    return "Context not_reported";
  }
  if (
    meter.context_length_availability !== "available" ||
    meter.context_length == null ||
    meter.pressure_ratio == null ||
    meter.pressure_availability === "not_reported"
  ) {
    return "Context not_reported";
  }
  const percent = Math.max(0, Math.round(meter.pressure_ratio * 100));
  if (meter.pressure_availability === "provider") {
    return `Context ${percent}% · provider`;
  }
  return `Context ~${percent}% · estimate`;
}
