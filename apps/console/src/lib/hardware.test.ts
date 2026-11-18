import { describe, expect, it } from "vitest";

import { NOT_REPORTED } from "./format";
import {
  type HardwareNode,
  formatByteMetric,
  formatMetric,
  listGpus,
  listStorage,
  metricNumber,
  summarizeFleet,
  summarizeNode,
} from "./hardware";

function available(value: string | number | boolean, unit?: string) {
  return { availability: "available", value, unit };
}

const gpuNode: HardwareNode = {
  worker_id: "worker-ai1",
  display_name: "AI1",
  connected: true,
  ready: true,
  status: "connected",
  heartbeat_age_seconds: 3,
  agent_version: "0.4.0",
  source: "worker_heartbeat",
  hardware: {
    collected_at: "2026-07-30T12:00:00Z",
    gpu_discovery_status: "available",
    host: {
      hostname: available("ai1"),
      operating_system: available("RHEL 9.4"),
      cpu_model: available("Intel Xeon Gold 6248"),
      cpu_count: available(80),
      cpu_utilization_percent: available(12.5, "percent"),
      memory_total_bytes: available(64 * 1024 ** 3),
      memory_used_bytes: available(16 * 1024 ** 3),
      memory_available_bytes: available(48 * 1024 ** 3),
      uptime_seconds: available(97200),
      nvidia_driver_version: available("550.90.07"),
      cuda_version: { availability: "not_supported" },
      storage: [
        {
          mountpoint: "/srv/yonder",
          total_bytes: available(2 * 1024 ** 4),
          used_bytes: available(1024 ** 4),
          available_bytes: available(1024 ** 4),
          health: available("ok"),
        },
      ],
      network_interfaces: [{ name: "eno1", is_up: available(true) }],
    },
    gpus: [
      {
        index: available(0),
        name: available("Tesla V100-SXM2-16GB"),
        memory_used_bytes: available(10 * 1024 ** 3),
        memory_total_bytes: available(16 * 1024 ** 3),
        memory_available_bytes: available(6 * 1024 ** 3),
        utilization_percent: available(35, "percent"),
      },
      {
        index: available(1),
        name: available("Tesla V100-SXM2-16GB"),
        memory_used_bytes: available(2 * 1024 ** 3),
        memory_total_bytes: available(16 * 1024 ** 3),
        memory_available_bytes: available(14 * 1024 ** 3),
        utilization_percent: { availability: "collection_failed" },
      },
    ],
  },
};

const macNode: HardwareNode = {
  worker_id: "worker-mac",
  display_name: "dev laptop",
  connected: true,
  ready: false,
  status: "connected",
  source: "worker_heartbeat",
  hardware: {
    gpu_discovery_status: "not_supported",
    gpu_discovery_message: "nvidia-smi is not present on this host",
    host: {
      hostname: available("mac-dev"),
      cpu_model: { availability: "collection_failed", message: "sysctl denied" },
      memory_used_bytes: available(8 * 1024 ** 3),
      memory_available_bytes: available(8 * 1024 ** 3),
    },
    gpus: [],
  },
};

const silentNode: HardwareNode = {
  worker_id: "worker-quiet",
  display_name: "",
  connected: false,
  status: "disconnected",
  source: "last_heartbeat_snapshot_worker_disconnected",
  hardware: null,
  note: "No hardware snapshot has been reported by this worker yet.",
};

describe("metric primitives", () => {
  it("preserves the existing formatMetric contract", () => {
    expect(
      formatMetric({ availability: "permission_unavailable", message: "access denied" }),
    ).toBe("permission_unavailable — access denied");
    expect(formatMetric(available(42, "percent"))).toBe("42 percent");
    expect(formatMetric(undefined)).toBe("not reported");
  });

  it("reads numbers only from available metrics", () => {
    expect(metricNumber(available(7))).toBe(7);
    expect(metricNumber({ availability: "not_reported" })).toBeNull();
    expect(metricNumber(available("seven"))).toBeNull();
  });

  it("renders byte metrics human-readably and keeps unavailable states", () => {
    expect(formatByteMetric(available(16 * 1024 ** 3))).toBe("16.0 GB");
    expect(formatByteMetric({ availability: "collection_failed" })).toBe(
      "collection_failed",
    );
    expect(formatByteMetric(undefined)).toBe(NOT_REPORTED);
  });
});

describe("summarizeNode", () => {
  it("builds a compact collapsed-card summary from reported metrics", () => {
    const summary = summarizeNode(gpuNode);
    expect(summary.hostname).toBe("ai1");
    expect(summary.cpuLabel).toBe("Intel Xeon Gold 6248 · 80 threads");
    expect(summary.memory.text).toBe("16.0 GB / 64.0 GB");
    expect(summary.memory.percent).toBe(25);
    expect(summary.uptimeText).toBe("1d 3h");
    expect(summary.statusTone).toBe("ok");
    expect(summary.gpu.count).toBe(2);
    expect(summary.gpu.modelLabel).toBe("2 × Tesla V100-SXM2-16GB");
    expect(summary.gpu.memoryText).toBe("12.0 GB / 32.0 GB");
    expect(summary.gpu.placementAvailable).toBe(true);
    expect(summary.heartbeatText).toBe("3s ago");
  });

  it("derives host memory total from used plus available when total is missing", () => {
    const summary = summarizeNode(macNode);
    expect(summary.memory.text).toBe("8.0 GB / 16.0 GB");
    expect(summary.memory.percent).toBe(50);
  });

  it("surfaces unavailable CPU and GPU states instead of placeholders", () => {
    const summary = summarizeNode(macNode);
    expect(summary.cpuLabel).toBe("collection_failed");
    expect(summary.gpu.count).toBe(0);
    expect(summary.gpu.modelLabel).toBe(NOT_REPORTED);
    expect(summary.gpu.memoryText).toBe(NOT_REPORTED);
    expect(summary.gpu.memoryPercent).toBeNull();
    expect(summary.gpu.placementAvailable).toBe(false);
    expect(summary.gpu.placementBlockedReason).toBe("not_supported");
  });

  it("marks disconnected workers without a snapshot", () => {
    const summary = summarizeNode(silentNode);
    expect(summary.hasSnapshot).toBe(false);
    expect(summary.statusTone).toBe("danger");
    expect(summary.hostname).toBe(NOT_REPORTED);
    expect(summary.displayName).toBe("worker-quiet");
  });

  it("blocks placement for maintenance workers", () => {
    const summary = summarizeNode({ ...gpuNode, maintenance: true });
    expect(summary.statusLabel).toBe("maintenance");
    expect(summary.statusTone).toBe("warn");
    expect(summary.gpu.placementBlockedReason).toBe("worker in maintenance");
  });
});

describe("detail listings", () => {
  it("normalises GPU rows and keeps missing utilisation null", () => {
    const gpus = listGpus(gpuNode);
    expect(gpus.map((gpu) => gpu.index)).toEqual([0, 1]);
    expect(gpus[0].memoryText).toBe("10.0 GB / 16.0 GB");
    expect(gpus[0].utilizationPercent).toBe(35);
    expect(gpus[1].utilizationPercent).toBeNull();
    expect(gpus[1].availableBytes).toBe(14 * 1024 ** 3);
  });

  it("falls back to array position when a GPU index is not reported", () => {
    const gpus = listGpus({
      ...gpuNode,
      hardware: {
        ...gpuNode.hardware,
        gpus: [{ index: { availability: "collection_failed" } }],
      },
    });
    expect(gpus[0].index).toBe(0);
    expect(gpus[0].name).toBe(NOT_REPORTED);
    expect(gpus[0].memoryText).toBe(NOT_REPORTED);
  });

  it("reads storage mounts with human-readable usage", () => {
    const mounts = listStorage(gpuNode);
    expect(mounts).toHaveLength(1);
    expect(mounts[0].mountpoint).toBe("/srv/yonder");
    expect(mounts[0].text).toBe("1.0 TB / 2.0 TB");
    expect(mounts[0].percent).toBe(50);
    expect(mounts[0].health).toBe("ok");
  });

  it("returns nothing when storage was never reported", () => {
    expect(listStorage(silentNode)).toEqual([]);
  });
});

describe("summarizeFleet", () => {
  it("aggregates only reported GPU memory", () => {
    const fleet = summarizeFleet([gpuNode, macNode, silentNode]);
    expect(fleet.nodes).toBe(3);
    expect(fleet.connected).toBe(2);
    expect(fleet.gpusReported).toBe(2);
    expect(fleet.gpuMemoryText).toBe("12.0 GB / 32.0 GB");
    expect(fleet.nodesWithoutSnapshot).toBe(1);
  });

  it("stays explicit when no worker reports GPU memory", () => {
    const fleet = summarizeFleet([macNode]);
    expect(fleet.gpusReported).toBe(0);
    expect(fleet.gpuMemoryText).toBe(NOT_REPORTED);
    expect(fleet.gpuMemoryPercent).toBeNull();
  });
});
