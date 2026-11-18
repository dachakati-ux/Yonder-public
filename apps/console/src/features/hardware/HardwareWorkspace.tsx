"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Gauge, MetricRow, StatTile, TonePill } from "@/components/metrics";
import { GatewayError } from "@/lib/gateway";
import { NOT_REPORTED, formatCount, formatPercent } from "@/lib/format";
import {
  type HardwareNode,
  listGpus,
  listNetworkInterfaces,
  listStorage,
  summarizeFleet,
  summarizeNode,
} from "@/lib/hardware";
import { fetchHardware } from "@/lib/ops";

function DetailSection({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <section className="node-section">
      <h3>{title}</h3>
      {meta ? <p className="entity-meta">{meta}</p> : null}
      {children}
    </section>
  );
}

function NodeCard({
  node,
  expanded,
  onToggle,
}: {
  node: HardwareNode;
  expanded: boolean;
  onToggle: (workerId: string, open: boolean) => void;
}) {
  const summary = summarizeNode(node);
  const host = (node.hardware?.host ?? {}) as Record<string, unknown>;
  const gpus = listGpus(node);
  const storage = listStorage(node);
  const interfaces = listNetworkInterfaces(node);
  const notes = node.hardware?.notes ?? [];
  const collectorErrors = node.hardware?.raw_collector_errors ?? [];

  return (
    <details
      className="node-card"
      open={expanded}
      onToggle={(event) => onToggle(node.worker_id, event.currentTarget.open)}
    >
      <summary className="node-summary">
        <div className="node-summary-head">
          <div className="node-identity">
            <strong>{summary.hostname === NOT_REPORTED ? summary.displayName : summary.hostname}</strong>
            <span className="entity-meta">{summary.workerId}</span>
          </div>
          <TonePill tone={summary.statusTone}>{summary.statusLabel}</TonePill>
        </div>

        <div className="node-spec-list">
          <div className="node-spec">
            <span>CPU</span>
            <strong title={summary.cpuLabel}>{summary.cpuLabel}</strong>
          </div>
          <Gauge
            label="Memory"
            percent={summary.memory.percent}
            detail={summary.memory.text}
          />
          <Gauge
            label={
              summary.gpu.count
                ? `GPU · ${formatCount(summary.gpu.count, "device")}`
                : "GPU"
            }
            percent={summary.gpu.memoryPercent}
            detail={
              summary.gpu.count
                ? `${summary.gpu.modelLabel} · ${summary.gpu.memoryText}`
                : `no GPUs reported (discovery: ${summary.gpu.discovery})`
            }
          />
        </div>

        <div className="node-summary-foot">
          <span className="entity-meta">
            heartbeat {summary.heartbeatText} · {node.source}
          </span>
          <span className="node-expand-hint" aria-hidden="true">
            {expanded ? "Hide details" : "Details"}
          </span>
        </div>
      </summary>

      <div className="node-detail">
        {node.last_error ? (
          <div className="message-error">{node.last_error}</div>
        ) : null}
        {node.note ? <p className="entity-meta">{node.note}</p> : null}

        <DetailSection
          title="Worker"
          meta={`ready: ${String(Boolean(node.ready))} · maintenance: ${String(
            Boolean(node.maintenance),
          )} · agent: ${node.agent_version ?? NOT_REPORTED} · source: ${node.source}`}
        >
          <div className="metric-grid">
            <MetricRow
              label="Display name"
              metric={{ availability: "available", value: summary.displayName }}
            />
            <MetricRow
              label="Last heartbeat"
              metric={{
                availability: node.last_heartbeat ? "available" : NOT_REPORTED,
                value: node.last_heartbeat ?? null,
              }}
            />
            <MetricRow
              label="Snapshot collected"
              metric={{
                availability: node.hardware?.collected_at ? "available" : NOT_REPORTED,
                value: node.hardware?.collected_at ?? null,
              }}
            />
            <MetricRow
              label="Uptime"
              metric={host.uptime_seconds}
              format="duration"
            />
          </div>
        </DetailSection>

        {!node.hardware ? (
          <p className="entity-meta">Hardware snapshot not reported.</p>
        ) : (
          <>
            <DetailSection title="Host">
              <div className="metric-grid">
                <MetricRow label="Hostname" metric={host.hostname} />
                <MetricRow label="OS" metric={host.operating_system} />
                <MetricRow label="Kernel" metric={host.kernel_version} />
                <MetricRow label="Architecture" metric={host.architecture} />
                <MetricRow label="CPU" metric={host.cpu_model} />
                <MetricRow label="CPU threads" metric={host.cpu_count} />
                <MetricRow
                  label="CPU util"
                  metric={host.cpu_utilization_percent}
                  format="percent"
                />
                <MetricRow label="Memory total" metric={host.memory_total_bytes} format="bytes" />
                <MetricRow label="Memory used" metric={host.memory_used_bytes} format="bytes" />
                <MetricRow
                  label="Memory available"
                  metric={host.memory_available_bytes}
                  format="bytes"
                />
                <MetricRow label="NVIDIA driver" metric={host.nvidia_driver_version} />
                <MetricRow label="CUDA" metric={host.cuda_version} />
              </div>
            </DetailSection>

            <DetailSection
              title={`GPU${gpus.length ? ` · ${formatCount(gpus.length, "device")}` : ""}`}
              meta={`discovery: ${summary.gpu.discovery}${
                summary.gpu.discoveryMessage ? ` — ${summary.gpu.discoveryMessage}` : ""
              }`}
            >
              {gpus.length === 0 ? (
                <p className="entity-meta">
                  No GPUs reported. On Mac development hosts this is expected.
                </p>
              ) : (
                <div className="stack">
                  {gpus.map((gpu) => (
                    <div key={`${node.worker_id}-gpu-${gpu.index}`} className="entity-card">
                      <div className="entity-title">
                        <span>
                          GPU {gpu.index} · {gpu.name}
                        </span>
                        <span className="entity-meta">
                          util{" "}
                          {gpu.utilizationPercent == null
                            ? NOT_REPORTED
                            : formatPercent(gpu.utilizationPercent)}
                        </span>
                      </div>
                      <Gauge
                        label="VRAM"
                        percent={gpu.memoryPercent}
                        detail={gpu.memoryText}
                      />
                      <div className="metric-grid" style={{ marginTop: "0.7rem" }}>
                        <MetricRow label="Name" metric={gpu.metrics.name} />
                        <MetricRow label="UUID" metric={gpu.metrics.uuid} />
                        <MetricRow label="Index" metric={gpu.metrics.index} />
                        <MetricRow
                          label="Utilization"
                          metric={gpu.metrics.utilization_percent}
                          format="percent"
                        />
                        <MetricRow
                          label="Memory used"
                          metric={gpu.metrics.memory_used_bytes}
                          format="bytes"
                        />
                        <MetricRow
                          label="Memory total"
                          metric={gpu.metrics.memory_total_bytes}
                          format="bytes"
                        />
                        <MetricRow
                          label="Memory available"
                          metric={gpu.metrics.memory_available_bytes}
                          format="bytes"
                        />
                        <MetricRow label="Temperature" metric={gpu.metrics.temperature_c} />
                        <MetricRow label="Power draw" metric={gpu.metrics.power_draw_watts} />
                        <MetricRow label="Power limit" metric={gpu.metrics.power_limit_watts} />
                        <MetricRow
                          label="Graphics clock"
                          metric={gpu.metrics.clock_graphics_mhz}
                        />
                        <MetricRow label="Memory clock" metric={gpu.metrics.clock_memory_mhz} />
                        <MetricRow label="ECC" metric={gpu.metrics.ecc_status} />
                        <MetricRow label="NVLink" metric={gpu.metrics.nvlink_state} />
                        <MetricRow
                          label="Active processes"
                          metric={gpu.metrics.active_processes}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </DetailSection>

            {storage.length ? (
              <DetailSection title="Storage">
                <div className="stack">
                  {storage.map((mount) => (
                    <div key={`${node.worker_id}-mount-${mount.mountpoint}`} className="entity-card">
                      <div className="entity-title">
                        <span>{mount.mountpoint}</span>
                        <span className="entity-meta">health: {mount.health}</span>
                      </div>
                      <Gauge label="Used" percent={mount.percent} detail={mount.text} />
                    </div>
                  ))}
                </div>
              </DetailSection>
            ) : null}

            {interfaces.length ? (
              <DetailSection title="Network">
                <div className="stack">
                  {interfaces.map((nic) => (
                    <div key={`${node.worker_id}-nic-${nic.name}`} className="entity-card">
                      <div className="entity-title">{nic.name}</div>
                      <div className="metric-grid">
                        <MetricRow label="Link up" metric={nic.metrics.is_up} />
                        <MetricRow label="Addresses" metric={nic.metrics.addresses} />
                        <MetricRow
                          label="Bytes sent"
                          metric={nic.metrics.bytes_sent}
                          format="bytes"
                        />
                        <MetricRow
                          label="Bytes received"
                          metric={nic.metrics.bytes_recv}
                          format="bytes"
                        />
                        <MetricRow
                          label="Throughput sent"
                          metric={nic.metrics.throughput_sent_bps}
                        />
                        <MetricRow
                          label="Throughput received"
                          metric={nic.metrics.throughput_recv_bps}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </DetailSection>
            ) : null}
          </>
        )}

        {node.runtime_summary ? (
          <DetailSection title="Runtime summary">
            <pre className="logs-panel">
              {JSON.stringify(node.runtime_summary, null, 2)}
            </pre>
          </DetailSection>
        ) : null}

        {notes.length || collectorErrors.length ? (
          <DetailSection title="Collector notes">
            {notes.length ? (
              <ul className="node-note-list">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
            {collectorErrors.length ? (
              <ul className="node-note-list node-note-list-error">
                {collectorErrors.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            ) : null}
          </DetailSection>
        ) : null}
      </div>
    </details>
  );
}

export function HardwareWorkspace() {
  const [nodes, setNodes] = useState<HardwareNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    const body = await fetchHardware();
    setNodes(body.nodes);
    setUpdatedAt(new Date().toLocaleTimeString());
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        await refresh();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof GatewayError
              ? `${err.message} [${err.code}]`
              : err instanceof Error
                ? err.message
                : "Failed to load hardware",
          );
        }
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refresh]);

  const fleet = useMemo(() => summarizeFleet(nodes), [nodes]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return nodes;
    return nodes.filter((node) => {
      const summary = summarizeNode(node);
      return [
        summary.hostname,
        summary.displayName,
        summary.workerId,
        summary.cpuLabel,
        summary.gpu.modelLabel,
        summary.statusLabel,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [filter, nodes]);

  const toggleNode = useCallback((workerId: string, open: boolean) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (open) next.add(workerId);
      else next.delete(workerId);
      return next;
    });
  }, []);

  const allExpanded = visible.length > 0 && visible.every((node) => expanded.has(node.worker_id));

  return (
    <div className="ops-layout">
      <header className="models-header">
        <div>
          <h1>Hardware</h1>
          <p>
            Live inventory from worker heartbeats only. Cards stay collapsed for
            fleet scanning; expand one for every metric the worker actually
            reported. Unavailable metrics stay explicit — Yonder never invents GPU
            values.
          </p>
        </div>
        <div className="model-chip">
          {updatedAt ? `updated ${updatedAt}` : "waiting…"} · poll 5s
        </div>
      </header>

      {error ? <div className="message-error models-error">{error}</div> : null}

      <section className="stat-strip" aria-label="Fleet summary">
        <StatTile
          label="Nodes"
          value={String(fleet.nodes)}
          hint={`${fleet.connected} connected`}
          tone={fleet.nodes === 0 ? "neutral" : fleet.connected === fleet.nodes ? "ok" : "warn"}
        />
        <StatTile
          label="Maintenance"
          value={String(fleet.maintenance)}
          hint={fleet.maintenance ? "excluded from placement" : "none"}
          tone={fleet.maintenance ? "warn" : "neutral"}
        />
        <StatTile
          label="GPUs reported"
          value={String(fleet.gpusReported)}
          hint={fleet.nodesWithoutSnapshot ? `${fleet.nodesWithoutSnapshot} without snapshot` : undefined}
          tone={fleet.gpusReported ? "ok" : "neutral"}
        />
        <StatTile
          label="Fleet VRAM"
          value={fleet.gpuMemoryText}
          hint={
            fleet.gpuMemoryPercent == null
              ? "not reported by workers"
              : `${formatPercent(fleet.gpuMemoryPercent)} in use`
          }
          tone={fleet.gpuMemoryPercent == null ? "neutral" : "ok"}
        />
      </section>

      {nodes.length ? (
        <div className="toolbar-row">
          <label className="toolbar-field">
            <span className="brand-sr">Filter nodes</span>
            <input
              className="status-select"
              type="search"
              placeholder="Filter by hostname, worker, CPU, or GPU"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn"
            aria-pressed={allExpanded}
            onClick={() =>
              setExpanded(
                allExpanded ? new Set() : new Set(visible.map((node) => node.worker_id)),
              )
            }
          >
            {allExpanded ? "Collapse all" : "Expand all"}
          </button>
        </div>
      ) : null}

      {nodes.length === 0 && !error ? (
        <div className="empty-state">
          <h3>No workers registered</h3>
          <p>
            Start the worker agent so it can heartbeat hardware inventory to the
            gateway. Until then, this page stays empty rather than inventing nodes.
          </p>
        </div>
      ) : null}

      {nodes.length > 0 && visible.length === 0 ? (
        <p className="entity-meta">No node matches this filter.</p>
      ) : null}

      <div className="node-grid">
        {visible.map((node) => (
          <NodeCard
            key={node.worker_id}
            node={node}
            expanded={expanded.has(node.worker_id)}
            onToggle={toggleNode}
          />
        ))}
      </div>
    </div>
  );
}
