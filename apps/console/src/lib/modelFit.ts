import { listGpus, type HardwareNode } from "@/lib/hardware";

/** Weight-only screening: 25% overhead, never add together devices.
 * Runtime, context and placement remain separate checks.
 */
export function weightBudgets(nodes: HardwareNode[]) {
  return nodes.flatMap((node) => {
    if (!node.connected || node.maintenance || !node.ready ||
        node.hardware?.gpu_discovery_status !== "available") return [];
    return listGpus(node).flatMap((gpu) => {
      if (!gpu.indexReported || gpu.availableBytes == null || gpu.availableBytes < 0 ||
          (gpu.totalBytes != null && gpu.availableBytes > gpu.totalBytes)) return [];
      return [{
        workerId: node.worker_id, worker: node.display_name, gpu: gpu.name, index: gpu.index,
        freeBytes: gpu.availableBytes,
        maxWeightBytes: Math.floor(gpu.availableBytes / 1.25),
      }];
    });
  }).sort((a, b) => b.maxWeightBytes - a.maxWeightBytes);
}

export function matchingWeightBudgets(nodes: HardwareNode[], weightBytes: number | null) {
  if (weightBytes == null || !Number.isFinite(weightBytes) || weightBytes <= 0) return [];
  return weightBudgets(nodes).filter((gpu) => gpu.maxWeightBytes >= weightBytes);
}
