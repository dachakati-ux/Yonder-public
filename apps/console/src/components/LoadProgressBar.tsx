"use client";

export function LoadProgressBar({
  percent,
  label,
  detail,
}: {
  percent: number | null;
  label: string;
  detail: string;
}) {
  const indeterminate = percent == null;
  const width = indeterminate ? undefined : Math.max(0, Math.min(100, percent));
  return (
    <div className="load-progress" role="status" aria-live="polite">
      <div className="load-progress-head">
        <span className="load-progress-label">{label}</span>
        <span className="load-progress-detail">{detail}</span>
      </div>
      <div
        className="load-progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : Math.round(width ?? 0)}
        aria-valuetext={detail}
      >
        <div
          className={
            indeterminate
              ? "load-progress-fill is-indeterminate"
              : "load-progress-fill"
          }
          style={indeterminate ? undefined : { width: `${width}%` }}
        />
      </div>
    </div>
  );
}

/** Latest denoise/running step percent from a generation job progress list. */
export function latestGenerationPercent(
  progress: Array<{ percent?: number | null }> | undefined,
): number | null {
  if (!progress?.length) return null;
  for (let i = progress.length - 1; i >= 0; i -= 1) {
    const value = progress[i]?.percent;
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

export function latestGenerationMessage(
  progress: Array<{ message?: string }> | undefined,
  fallback: string,
): string {
  if (!progress?.length) return fallback;
  for (let i = progress.length - 1; i >= 0; i -= 1) {
    const message = progress[i]?.message?.trim();
    if (message) return message;
  }
  return fallback;
}
