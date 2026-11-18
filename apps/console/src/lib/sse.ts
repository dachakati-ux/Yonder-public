import type { YonderErrorBody } from "@/types";
import type { ContextEvent, ContextMeter } from "@/lib/contextMeter";

export type ParsedSsePayload =
  | { kind: "done" }
  | { kind: "error"; error: YonderErrorBody["error"] }
  | { kind: "token"; token: string }
  | { kind: "context"; event: ContextEvent }
  | { kind: "usage"; usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number } }
  | { kind: "empty" };

export function extractSsePayloads(buffer: string): {
  payloads: string[];
  remainder: string;
} {
  const normalized = buffer.replaceAll("\r\n", "\n");
  const lines = normalized.split("\n");
  const remainder = lines.pop() ?? "";
  const payloads = lines
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());
  return { payloads, remainder };
}

export function parseSsePayload(payload: string): ParsedSsePayload {
  if (payload === "[DONE]") {
    return { kind: "done" };
  }
  const parsed = JSON.parse(payload) as {
    object?: string;
    error?: YonderErrorBody["error"];
    choices?: Array<{ delta?: { content?: string | null } }>;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    } | null;
    condensed?: boolean;
    meter?: ContextMeter;
    messages?: Array<{ id: string; role: string; content: string }>;
  };
  if (parsed.error) {
    return { kind: "error", error: parsed.error };
  }
  if (parsed.object === "yonder.context" && parsed.meter) {
    return {
      kind: "context",
      event: {
        condensed: Boolean(parsed.condensed),
        meter: parsed.meter,
        messages: parsed.messages ?? [],
      },
    };
  }
  const token = parsed.choices?.[0]?.delta?.content;
  if (token) {
    return { kind: "token", token };
  }
  if (parsed.usage && typeof parsed.usage.prompt_tokens === "number") {
    return {
      kind: "usage",
      usage: {
        prompt_tokens: parsed.usage.prompt_tokens,
        completion_tokens: parsed.usage.completion_tokens ?? 0,
        total_tokens:
          parsed.usage.total_tokens ??
          parsed.usage.prompt_tokens + (parsed.usage.completion_tokens ?? 0),
      },
    };
  }
  return { kind: "empty" };
}
