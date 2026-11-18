import type { ReactNode } from "react";

import { NOT_REPORTED, formatPercent } from "@/lib/format";
import {
  type StatusTone,
  formatByteMetric,
  formatDurationMetric,
  formatMetric,
  formatPercentMetric,
  isMetricAvailable,
} from "@/lib/hardware";

export type MetricFormat = "raw" | "bytes" | "percent" | "duration";

function renderMetric(metric: unknown, format: MetricFormat): string {
  switch (format) {
    case "bytes":
      return formatByteMetric(metric);
    case "percent":
      return formatPercentMetric(metric);
    case "duration":
      return formatDurationMetric(metric);
    default:
      return formatMetric(metric);
  }
}

export function MetricRow({
  label,
  metric,
  format = "raw",
}: {
  label: string;
  metric: unknown;
  format?: MetricFormat;
}) {
  const unavailable = !isMetricAvailable(metric);
  return (
    <div className={`metric-row ${unavailable ? "metric-unavailable" : ""}`}>
      <span>{label}</span>
      <strong>{renderMetric(metric, format)}</strong>
    </div>
  );
}

/**
 * Usage bar. A null percent renders no bar at all so an unreported metric is
 * never drawn as an empty (and therefore misleading) gauge.
 */
export function Gauge({
  label,
  percent,
  detail,
  tone,
}: {
  label: string;
  percent: number | null;
  detail: string;
  tone?: StatusTone;
}) {
  const resolvedTone: StatusTone =
    tone ?? (percent == null ? "neutral" : percent >= 90 ? "danger" : percent >= 70 ? "warn" : "ok");
  return (
    <div className="gauge">
      <div className="gauge-head">
        <span className="gauge-label">{label}</span>
        <span className={`gauge-value ${percent == null ? "gauge-value-unknown" : ""}`}>
          {detail}
        </span>
      </div>
      {percent == null ? (
        <div className="gauge-empty">{NOT_REPORTED}</div>
      ) : (
        <div
          className="gauge-track"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
          aria-valuetext={`${label}: ${detail} (${formatPercent(percent)})`}
        >
          <div
            className="gauge-fill"
            data-tone={resolvedTone}
            style={{ width: `${Math.min(100, Math.max(2, percent))}%` }}
          />
        </div>
      )}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: StatusTone;
}) {
  return (
    <div className="stat-tile" data-tone={tone}>
      <span className="stat-tile-label">{label}</span>
      <strong className="stat-tile-value">{value}</strong>
      {hint ? <span className="stat-tile-hint">{hint}</span> : null}
    </div>
  );
}

export function TonePill({
  tone,
  children,
}: {
  tone: StatusTone;
  children: ReactNode;
}) {
  return (
    <span className="status-pill" data-tone={tone}>
      {children}
    </span>
  );
}
