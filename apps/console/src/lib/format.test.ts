import { describe, expect, it } from "vitest";

import {
  NOT_REPORTED,
  formatBytes,
  formatCount,
  formatDuration,
  formatPercent,
  ratioPercent,
  titleCase,
} from "./format";

describe("formatBytes", () => {
  it("scales into human units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(6 * 1024 ** 2)).toBe("6.0 MB");
    expect(formatBytes(16 * 1024 ** 3)).toBe("16.0 GB");
    expect(formatBytes(2 * 1024 ** 4)).toBe("2.0 TB");
  });

  it("never invents a size for missing or invalid input", () => {
    expect(formatBytes(null)).toBe(NOT_REPORTED);
    expect(formatBytes(undefined)).toBe(NOT_REPORTED);
    expect(formatBytes(Number.NaN)).toBe(NOT_REPORTED);
    expect(formatBytes(-1)).toBe(NOT_REPORTED);
  });
});

describe("formatPercent and ratioPercent", () => {
  it("formats reported percentages", () => {
    expect(formatPercent(42)).toBe("42%");
    expect(formatPercent(42.42, 1)).toBe("42.4%");
  });

  it("keeps missing percentages explicit", () => {
    expect(formatPercent(null)).toBe(NOT_REPORTED);
  });

  it("derives a ratio only when both sides are reported", () => {
    expect(ratioPercent(1024, 2048)).toBe(50);
    expect(ratioPercent(null, 2048)).toBeNull();
    expect(ratioPercent(1024, null)).toBeNull();
    expect(ratioPercent(1024, 0)).toBeNull();
  });

  it("clamps out-of-range ratios", () => {
    expect(ratioPercent(3000, 2048)).toBe(100);
    expect(ratioPercent(-10, 2048)).toBe(0);
  });
});

describe("formatDuration", () => {
  it("renders coarse units first", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(90)).toBe("1m 30s");
    expect(formatDuration(3720)).toBe("1h 2m");
    expect(formatDuration(97200)).toBe("1d 3h");
  });

  it("keeps missing uptime explicit", () => {
    expect(formatDuration(null)).toBe(NOT_REPORTED);
  });
});

describe("small text helpers", () => {
  it("pluralises counts", () => {
    expect(formatCount(1, "GPU")).toBe("1 GPU");
    expect(formatCount(2, "GPU")).toBe("2 GPUs");
    expect(formatCount(2, "device", "devices")).toBe("2 devices");
  });

  it("title-cases availability-style identifiers", () => {
    expect(titleCase("permission_unavailable")).toBe("Permission Unavailable");
  });
});
