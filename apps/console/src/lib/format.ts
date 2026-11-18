/**
 * Presentation-only formatting helpers.
 *
 * These functions never substitute a value for missing data: callers pass
 * `null` when a metric was not reported and receive `NOT_REPORTED` back so the
 * console keeps unavailable states explicit.
 */

export const NOT_REPORTED = "not_reported";

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/**
 * Byte sizes in binary multiples (1 GB = 1024 MB) with short unit labels, the
 * convention operators already read from `df -h`, `free -h`, and nvidia-smi.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return NOT_REPORTED;
  if (bytes === 0) return "0 B";
  let unitIndex = 0;
  let value = bytes;
  while (value >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${BYTE_UNITS[unitIndex]}`;
}

export function formatPercent(
  percent: number | null | undefined,
  digits = 0,
): string {
  if (percent == null || !Number.isFinite(percent)) return NOT_REPORTED;
  return `${percent.toFixed(digits)}%`;
}

/** Share of `total` consumed by `used`, or null when either side is missing. */
export function ratioPercent(
  used: number | null | undefined,
  total: number | null | undefined,
): number | null {
  if (used == null || total == null) return null;
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return null;
  return Math.min(100, Math.max(0, (used / total) * 100));
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) {
    return NOT_REPORTED;
  }
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

/** Pluralised counter, e.g. `2 GPUs` / `1 GPU`. */
export function formatCount(count: number, singular: string, plural?: string): string {
  const label = count === 1 ? singular : (plural ?? `${singular}s`);
  return `${count} ${label}`;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
