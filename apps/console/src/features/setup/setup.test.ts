import { describe, expect, it } from "vitest";
import type { HardwareNode } from "@/lib/hardware";
import { matchingWeightBudgets, weightBudgets } from "@/lib/modelFit";
import { readyModels, type SetupStatus } from "./setup";

const status: SetupStatus = {
  gateway_ready: true, security_mode: "development", aliases: [
    { alias: "mock", kind: "mock", status: "route_ready", experimental: true },
    { alias: "real", kind: "model", status: "route_ready", experimental: false },
    { alias: "offline", kind: "model", status: "unavailable", experimental: false },
  ],
};
const metric = (value: number | string) => ({ availability: "available", value });
const node: HardwareNode = {
  worker_id: "worker", display_name: "Worker", connected: true, ready: true,
  status: "connected", source: "worker_heartbeat",
  hardware: { gpu_discovery_status: "available", gpus: [{
    index: metric(0), name: metric("GPU"), memory_available_bytes: metric(10 * 1024 ** 3),
    memory_total_bytes: metric(16 * 1024 ** 3),
  }] },
};
describe("setup diagnostics", () => {
  it("never treats mock or unavailable routes as real candidates", () => {
    expect(readyModels(status).map((alias) => alias.alias)).toEqual(["real"]);
    expect(readyModels({ ...status, gateway_ready: false })).toEqual([]);
  });
  it("screens weights against one GPU and leaves overhead", () => {
    expect(weightBudgets([node])[0].maxWeightBytes).toBe(8 * 1024 ** 3);
    expect(matchingWeightBudgets([node], 8 * 1024 ** 3)).toHaveLength(1);
    expect(matchingWeightBudgets([node, node], 9 * 1024 ** 3)).toHaveLength(0);
  });
  it("rejects disconnected, unready and maintenance nodes", () => {
    expect(weightBudgets([
      { ...node, connected: false }, { ...node, ready: false }, { ...node, maintenance: true },
    ])).toEqual([]);
  });
  it("rejects missing indexes, missing memory and degraded discovery", () => {
    const gpu = node.hardware!.gpus![0];
    for (const gpus of [
      [{ ...gpu, index: undefined }],
      [{ ...gpu, memory_available_bytes: { availability: "not_reported" } }],
      [{ ...gpu, memory_available_bytes: metric(17 * 1024 ** 3) }],
    ]) {
      expect(weightBudgets([{ ...node, hardware: { gpu_discovery_status: "available", gpus } }]))
        .toEqual([]);
    }
    expect(weightBudgets([{ ...node, hardware: { ...node.hardware, gpu_discovery_status: "not_reported" } }])).toEqual([]);
    expect(matchingWeightBudgets([node], null)).toEqual([]);
  });
});
