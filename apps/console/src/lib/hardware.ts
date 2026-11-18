/**
 * Hardware metric primitives and node summaries.
 *
 * Every helper here is pure and derives values only from what a worker actually
 * reported. When a metric is missing or explicitly unavailable the availability
 * string is surfaced instead of a placeholder number.
 */

import {
  NOT_REPORTED,
  formatBytes,
  formatDuration,
  formatPercent,
  ratioPercent,
} from "@/lib/format";

export type MetricValue = {
  availability: string;
  value?: string | number | boolean | null;
  unit?: string | null;
  message?: string | null;
};

export type HardwareNode = {
  worker_id: string;
  display_name: string;
  connected: boolean;
  status: string;
  maintenance?: boolean;
  ready?: boolean;
  last_heartbeat?: string | null;
  heartbeat_age_seconds?: number | null;
  agent_version?: string;
  runtime_summary?: Record<string, unknown> | null;
  last_error?: string | null;
  hardware?: {
    collected_at?: string;
    gpu_discovery_status?: string;
    gpu_discovery_message?: string | null;
    notes?: string[];
    raw_collector_errors?: string[];
    host?: Record<string, MetricValue | unknown>;
    gpus?: Array<Record<string, MetricValue | unknown>>;
  } | null;
  source: string;
  note?: string | null;
};

export type StatusTone = "ok" | "warn" | "danger" | "neutral";

function asMetric(metric: unknown): MetricValue | null {
  if (!metric || typeof metric !== "object") return null;
  const candidate = metric as MetricValue;
  return typeof candidate.availability === "string" ? candidate : null;
}

export function metricAvailability(metric: unknown): string {
  return asMetric(metric)?.availability ?? NOT_REPORTED;
}

export function isMetricAvailable(metric: unknown): boolean {
  return metricAvailability(metric) === "available";
}

export function metricNumber(metric: unknown): number | null {
  const item = asMetric(metric);
  if (!item || item.availability !== "available") return null;
  return typeof item.value === "number" && Number.isFinite(item.value)
    ? item.value
    : null;
}

/** Reported text for an available metric, or null so callers stay explicit. */
export function metricText(metric: unknown): string | null {
  const item = asMetric(metric);
  if (!item || item.availability !== "available") return null;
  if (item.value == null || item.value === "") return null;
  return String(item.value);
}

export function formatMetric(metric: MetricValue | unknown): string {
  if (!metric || typeof metric !== "object") {
    return "not reported";
  }
  const value = metric as MetricValue;
  if (value.availability !== "available") {
    const suffix = value.message ? ` — ${value.message}` : "";
    return `${value.availability}${suffix}`;
  }
  if (value.unit) {
    return `${value.value} ${value.unit}`;
  }
  return String(value.value);
}

/** Byte metric rendered human-readably, keeping unavailable states verbatim. */
export function formatByteMetric(metric: unknown): string {
  const bytes = metricNumber(metric);
  if (bytes != null) return formatBytes(bytes);
  return metricAvailability(metric);
}

export function formatPercentMetric(metric: unknown): string {
  const percent = metricNumber(metric);
  if (percent != null) return formatPercent(percent);
  return metricAvailability(metric);
}

export function formatDurationMetric(metric: unknown): string {
  const seconds = metricNumber(metric);
  if (seconds != null) return formatDuration(seconds);
  return metricAvailability(metric);
}

export type MemoryUsage = {
  usedBytes: number | null;
  totalBytes: number | null;
  availableBytes: number | null;
  percent: number | null;
  text: string;
};

function memoryText(used: number | null, total: number | null): string {
  if (used != null && total != null) {
    return `${formatBytes(used)} / ${formatBytes(total)}`;
  }
  if (used != null) return `${formatBytes(used)} used`;
  if (total != null) return `${formatBytes(total)} total`;
  return NOT_REPORTED;
}

function hostRecord(node: HardwareNode): Record<string, unknown> {
  return (node.hardware?.host ?? {}) as Record<string, unknown>;
}

export function summarizeMemory(node: HardwareNode): MemoryUsage {
  const host = hostRecord(node);
  const usedBytes = metricNumber(host.memory_used_bytes);
  const totalBytes = metricNumber(host.memory_total_bytes);
  const availableBytes = metricNumber(host.memory_available_bytes);
  const resolvedTotal =
    totalBytes ??
    (usedBytes != null && availableBytes != null ? usedBytes + availableBytes : null);
  return {
    usedBytes,
    totalBytes: resolvedTotal,
    availableBytes,
    percent: ratioPercent(usedBytes, resolvedTotal),
    text: memoryText(usedBytes, resolvedTotal),
  };
}

export type GpuView = {
  index: number;
  /**
   * False when the worker did not report an index and the array position was
   * used for display. The Gateway cannot place onto such a device.
   */
  indexReported: boolean;
  name: string;
  usedBytes: number | null;
  totalBytes: number | null;
  availableBytes: number | null;
  memoryText: string;
  memoryPercent: number | null;
  utilizationPercent: number | null;
  temperatureC: number | null;
  metrics: Record<string, unknown>;
};

export function listGpus(node: HardwareNode): GpuView[] {
  const gpus = node.hardware?.gpus ?? [];
  return gpus.map((gpu, fallbackIndex) => {
    const metrics = gpu as Record<string, unknown>;
    const usedBytes = metricNumber(metrics.memory_used_bytes);
    const totalBytes = metricNumber(metrics.memory_total_bytes);
    const reportedIndex = metricNumber(metrics.index);
    return {
      index: reportedIndex ?? fallbackIndex,
      indexReported: reportedIndex != null,
      name: metricText(metrics.name) ?? metricAvailability(metrics.name),
      usedBytes,
      totalBytes,
      availableBytes: metricNumber(metrics.memory_available_bytes),
      memoryText: memoryText(usedBytes, totalBytes),
      memoryPercent: ratioPercent(usedBytes, totalBytes),
      utilizationPercent: metricNumber(metrics.utilization_percent),
      temperatureC: metricNumber(metrics.temperature_c),
      metrics,
    };
  });
}

export type GpuSummary = {
  count: number;
  discovery: string;
  discoveryMessage: string | null;
  modelLabel: string;
  memoryText: string;
  memoryPercent: number | null;
  placementAvailable: boolean;
  placementBlockedReason: string | null;
};

export function summarizeGpus(node: HardwareNode): GpuSummary {
  const gpus = listGpus(node);
  const discovery = node.hardware?.gpu_discovery_status ?? NOT_REPORTED;
  const named = new Map<string, number>();
  for (const gpu of gpus) {
    if (metricText(gpu.metrics.name) == null) continue;
    named.set(gpu.name, (named.get(gpu.name) ?? 0) + 1);
  }
  const modelLabel = named.size
    ? [...named.entries()]
        .map(([name, count]) => (count > 1 ? `${count} × ${name}` : name))
        .join(" · ")
    : NOT_REPORTED;

  const reportedMemory = gpus.filter(
    (gpu) => gpu.usedBytes != null && gpu.totalBytes != null,
  );
  const used = reportedMemory.reduce((sum, gpu) => sum + (gpu.usedBytes ?? 0), 0);
  const total = reportedMemory.reduce((sum, gpu) => sum + (gpu.totalBytes ?? 0), 0);
  const hasMemory = reportedMemory.length > 0;

  // The Gateway discards the whole GPU list unless discovery reports
  // "available", so a degraded status blocks placement even if devices appear.
  let placementBlockedReason: string | null = null;
  if (node.maintenance) {
    placementBlockedReason = "worker in maintenance";
  } else if (!node.connected) {
    placementBlockedReason = "worker_disconnected";
  } else if (discovery !== "available") {
    placementBlockedReason = discovery;
  } else if (gpus.length === 0) {
    placementBlockedReason = "no GPUs reported";
  }

  return {
    count: gpus.length,
    discovery,
    discoveryMessage: node.hardware?.gpu_discovery_message ?? null,
    modelLabel,
    memoryText: hasMemory ? memoryText(used, total) : NOT_REPORTED,
    memoryPercent: hasMemory ? ratioPercent(used, total) : null,
    placementAvailable: placementBlockedReason === null,
    placementBlockedReason,
  };
}

export type NodeSummary = {
  workerId: string;
  displayName: string;
  hostname: string;
  statusLabel: string;
  statusTone: StatusTone;
  hasSnapshot: boolean;
  operatingSystem: string;
  cpuLabel: string;
  cpuUtilizationPercent: number | null;
  uptimeText: string;
  memory: MemoryUsage;
  gpu: GpuSummary;
  heartbeatText: string;
};

export function summarizeNode(node: HardwareNode): NodeSummary {
  const host = hostRecord(node);
  const cores = metricNumber(host.cpu_count);
  const cpuModel = metricText(host.cpu_model);
  const cpuLabel = cpuModel
    ? cores != null
      ? `${cpuModel} · ${cores} threads`
      : cpuModel
    : metricAvailability(host.cpu_model);
  const maintenance = Boolean(node.maintenance);
  const statusLabel = maintenance ? "maintenance" : node.status;
  const statusTone: StatusTone = maintenance
    ? "warn"
    : !node.connected
      ? "danger"
      : node.ready
        ? "ok"
        : "warn";

  return {
    workerId: node.worker_id,
    displayName: node.display_name || node.worker_id,
    hostname: metricText(host.hostname) ?? metricAvailability(host.hostname),
    statusLabel,
    statusTone,
    hasSnapshot: Boolean(node.hardware),
    operatingSystem:
      metricText(host.operating_system) ?? metricAvailability(host.operating_system),
    cpuLabel,
    cpuUtilizationPercent: metricNumber(host.cpu_utilization_percent),
    uptimeText: formatDurationMetric(host.uptime_seconds),
    memory: summarizeMemory(node),
    gpu: summarizeGpus(node),
    heartbeatText:
      node.heartbeat_age_seconds != null
        ? `${Math.round(node.heartbeat_age_seconds)}s ago`
        : (node.last_heartbeat ?? NOT_REPORTED),
  };
}

export type StorageView = {
  mountpoint: string;
  totalBytes: number | null;
  usedBytes: number | null;
  availableBytes: number | null;
  percent: number | null;
  text: string;
  health: string;
};

export function listStorage(node: HardwareNode): StorageView[] {
  const host = hostRecord(node);
  const entries = Array.isArray(host.storage) ? host.storage : [];
  return entries.map((entry) => {
    const record = (entry ?? {}) as Record<string, unknown>;
    const usedBytes = metricNumber(record.used_bytes);
    const totalBytes = metricNumber(record.total_bytes);
    return {
      mountpoint: typeof record.mountpoint === "string" ? record.mountpoint : NOT_REPORTED,
      totalBytes,
      usedBytes,
      availableBytes: metricNumber(record.available_bytes),
      percent: ratioPercent(usedBytes, totalBytes),
      text: memoryText(usedBytes, totalBytes),
      health: formatMetric(record.health),
    };
  });
}

export type NetworkInterfaceView = {
  name: string;
  metrics: Record<string, unknown>;
};

export function listNetworkInterfaces(node: HardwareNode): NetworkInterfaceView[] {
  const host = hostRecord(node);
  const entries = Array.isArray(host.network_interfaces)
    ? host.network_interfaces
    : [];
  return entries.map((entry) => {
    const record = (entry ?? {}) as Record<string, unknown>;
    return {
      name: typeof record.name === "string" ? record.name : NOT_REPORTED,
      metrics: record,
    };
  });
}

export type FleetSummary = {
  nodes: number;
  connected: number;
  maintenance: number;
  gpusReported: number;
  gpuMemoryText: string;
  gpuMemoryPercent: number | null;
  nodesWithoutSnapshot: number;
};

export function summarizeFleet(nodes: HardwareNode[]): FleetSummary {
  let gpusReported = 0;
  let used = 0;
  let total = 0;
  let hasMemory = false;
  for (const node of nodes) {
    for (const gpu of listGpus(node)) {
      gpusReported += 1;
      if (gpu.usedBytes != null && gpu.totalBytes != null) {
        used += gpu.usedBytes;
        total += gpu.totalBytes;
        hasMemory = true;
      }
    }
  }
  return {
    nodes: nodes.length,
    connected: nodes.filter((node) => node.connected).length,
    maintenance: nodes.filter((node) => Boolean(node.maintenance)).length,
    gpusReported,
    gpuMemoryText: hasMemory ? memoryText(used, total) : NOT_REPORTED,
    gpuMemoryPercent: hasMemory ? ratioPercent(used, total) : null,
    nodesWithoutSnapshot: nodes.filter((node) => !node.hardware).length,
  };
}
