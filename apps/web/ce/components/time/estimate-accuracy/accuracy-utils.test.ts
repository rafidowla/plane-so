/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import {
  bucketLabel,
  errorTone,
  formatAbsError,
  formatError,
  formatMinutes,
  formatRatio,
  formatShare,
  headline,
  scopeNote,
  trendChartData,
} from "./accuracy-utils";
import type { TAccuracyStats, TAccuracyTrendPoint } from "./types";

const stats = (over: Partial<TAccuracyStats> = {}): TAccuracyStats => ({
  n: 5,
  original_minutes: 100,
  current_minutes: 100,
  actual_minutes: 125,
  ratio: 1.25,
  error_pct: 25,
  ratio_current: 1.25,
  median_abs_error_pct: 25,
  median_abs_error_pct_current: 25,
  within_count: 2,
  within_share: 0.4,
  solo_count: 1,
  low_sample: false,
  ...over,
});

describe("formatting", () => {
  it("formats minutes", () => {
    expect(formatMinutes(0)).toBe("0m");
    expect(formatMinutes(45)).toBe("45m");
    expect(formatMinutes(60)).toBe("1h");
    expect(formatMinutes(90)).toBe("1h 30m");
    expect(formatMinutes(null)).toBe("–");
  });
  it("formats ratio, errors and shares", () => {
    expect(formatRatio(1.25)).toBe("125%");
    expect(formatRatio(null)).toBe("–");
    expect(formatError(25)).toBe("+25%");
    expect(formatError(-12.54)).toBe("-12.5%");
    expect(formatError(0)).toBe("0%");
    expect(formatError(0.04)).toBe("0%");
    expect(formatAbsError(40)).toBe("40%");
    expect(formatShare(0.25)).toBe("25%");
    expect(formatShare(null)).toBe("–");
  });
});

describe("errorTone", () => {
  it("treats the band edge as good", () => {
    expect(errorTone(20, 20)).toBe("good");
    expect(errorTone(-20, 20)).toBe("good");
    expect(errorTone(20.1, 20)).toBe("over");
    expect(errorTone(-20.1, 20)).toBe("under");
    expect(errorTone(null, 20)).toBe("none");
  });
});

describe("bucketLabel", () => {
  it("labels weeks and months without timezone shifts", () => {
    expect(bucketLabel("2026-06-22", "week")).toBe("22 Jun");
    expect(bucketLabel("2026-01-05", "week")).toBe("5 Jan");
    expect(bucketLabel("2026-06-01", "month")).toBe("Jun 2026");
  });
});

describe("trendChartData", () => {
  const trend: TAccuracyTrendPoint[] = [
    { ...stats({ n: 4, low_sample: true, ratio: 1.1, within_share: 0.5 }), bucket: "2026-06-15" },
    { ...stats({ n: 6, ratio: 0.9, within_share: 0.75 }), bucket: "2026-06-22" },
    { ...stats({ n: 0, ratio: null, within_share: null }), bucket: "2026-06-29" },
  ];
  it("charts the ratio as a percent of the estimate and flags low samples", () => {
    expect(trendChartData(trend, "week", "ratio")).toEqual([
      { name: "15 Jun", value: 110, n: 4, low_sample: 1, tooltip: "110% · 4 tasks (too few to read)" },
      { name: "22 Jun", value: 90, n: 6, low_sample: 0, tooltip: "90% · 6 tasks" },
    ]);
  });
  it("charts the within-band share", () => {
    expect(trendChartData(trend, "week", "within").map((d) => d.value)).toEqual([50, 75]);
  });
});

describe("copy", () => {
  it("describes scope", () => {
    expect(scopeNote("self")).toMatch(/your own/i);
    expect(scopeNote("projects")).toMatch(/administer/);
    expect(scopeNote("all")).toMatch(/everyone/i);
  });
  it("summarises the headline", () => {
    expect(headline(stats({ ratio: 1.5 }), 20)).toContain("150%");
    expect(headline(stats({ ratio: 1.5 }), 20)).toContain("longer");
    expect(headline(stats({ ratio: 0.5 }), 20)).toContain("faster");
    expect(headline(stats({ ratio: 1.05 }), 20)).toContain("close");
    expect(headline(stats({ n: 0, ratio: null }), 20)).toBeNull();
  });
});
