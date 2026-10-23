/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S7 — pure helpers for the Estimates tab (formatting, tones, trend chart data).
import type { TAccuracyGranularity, TAccuracyStats, TAccuracyTrendPoint } from "./types";

/** Minutes as "1h 30m" / "45m" / "2h"; "–" when unknown. */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "–";
  const total = Math.max(0, Math.round(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/** Ratio (actual ÷ estimate) as a whole-ish percent: 1.25 -> "125%". */
export function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "–";
  return `${Math.round(ratio * 100)}%`;
}

/** Signed error percent: 25 -> "+25%", -12.5 -> "-12.5%", 0 -> "0%". */
export function formatError(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "–";
  const rounded = Math.round(pct * 10) / 10;
  if (rounded === 0) return "0%";
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

/** Unsigned percent for the median absolute error: 40 -> "40%". */
export function formatAbsError(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "–";
  return `${Math.round(pct * 10) / 10}%`;
}

/** A 0..1 share as a whole percent: 0.25 -> "25%". */
export function formatShare(share: number | null | undefined): string {
  if (share === null || share === undefined) return "–";
  return `${Math.round(share * 100)}%`;
}

export type TErrorTone = "good" | "over" | "under" | "none";

/** Colour class family for an error: within the band is good; slower than estimated is "over", faster "under". */
export function errorTone(errorPct: number | null | undefined, withinPct: number): TErrorTone {
  if (errorPct === null || errorPct === undefined) return "none";
  if (Math.abs(errorPct) <= withinPct) return "good";
  return errorPct > 0 ? "over" : "under";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-06-22" -> "22 Jun" (week) / "Jun 2026" (month). No Date parsing, so no timezone shifts. */
export function bucketLabel(bucket: string, granularity: TAccuracyGranularity): string {
  const [year, month, day] = bucket.split("-").map(Number);
  const name = MONTHS[(month ?? 1) - 1] ?? "";
  return granularity === "month" ? `${name} ${year}` : `${day} ${name}`;
}

export type TTrendMetric = "ratio" | "within";

export type TTrendDatum = {
  name: string;
  /** chart value in percent */
  value: number;
  n: number;
  /** 1 when the bucket has too few tasks to read (a number: the chart's data type allows only string | number) */
  low_sample: 0 | 1;
  tooltip: string;
};

/** Chart rows for the trend: ratio as percent of the estimate, or the within-band share. Empty buckets are skipped. */
export function trendChartData(
  trend: TAccuracyTrendPoint[],
  granularity: TAccuracyGranularity,
  metric: TTrendMetric
): TTrendDatum[] {
  const data: TTrendDatum[] = [];
  for (const point of trend) {
    const raw = metric === "ratio" ? point.ratio : point.within_share;
    if (raw === null || raw === undefined) continue;
    const value = Math.round(raw * 1000) / 10;
    data.push({
      name: bucketLabel(point.bucket, granularity),
      value,
      n: point.n,
      low_sample: point.low_sample ? 1 : 0,
      tooltip: `${value}% · ${point.n} ${point.n === 1 ? "task" : "tasks"}${point.low_sample ? " (too few to read)" : ""}`,
    });
  }
  return data;
}

/** One-line description of the scope the server applied, for the page header. */
export function scopeNote(scope: "all" | "projects" | "self"): string {
  if (scope === "all") return "Everyone's estimates, credited to whoever set the original estimate.";
  if (scope === "projects") return "Estimates in projects you administer, plus your own.";
  return "Your own estimates.";
}

/** Short read of the headline numbers for coaching, or null with no data. */
export function headline(summary: TAccuracyStats, withinPct: number): string | null {
  if (summary.n === 0 || summary.ratio === null) return null;
  const pct = Math.round(summary.ratio * 100);
  const how =
    pct > 100 + withinPct
      ? "longer than estimated"
      : pct < 100 - withinPct
        ? "faster than estimated"
        : "close to the estimates";
  return `Work took ${pct}% of the estimated time overall: ${how}.`;
}
