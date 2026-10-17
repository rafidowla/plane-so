/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — pure helpers for the Reports tab (no `@/` imports: unit-tested by vitest).
import type { TReportScope, TReportSeriesPoint } from "./types";

/** Decimal hours to one place ("37.5 h"): the report unit (DESIGN item 2). */
export function formatHours(minutes: number | null | undefined): string {
  return `${((minutes ?? 0) / 60).toFixed(1)} h`;
}

/** Signed decimal hours ("+2.5 h", "-4.0 h", "0.0 h") for a balance. */
export function formatSignedHours(minutes: number | null | undefined): string {
  const value = minutes ?? 0;
  const text = (Math.abs(value) / 60).toFixed(1);
  if (Number(text) === 0) return "0.0 h";
  return `${value < 0 ? "-" : "+"}${text} h`;
}

/** "7h 30m" for small amounts (running timers, weekly capacity). */
export function formatHm(minutes: number | null | undefined): string {
  const total = Math.max(0, Math.round(minutes ?? 0));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

export type TBalanceTone = "positive" | "negative" | "neutral";

/** Over expected = positive, under = negative; within 6 minutes (0.1 h) is neutral. */
export function balanceTone(minutes: number | null | undefined): TBalanceTone {
  const value = minutes ?? 0;
  if (Math.abs(value) < 6) return "neutral";
  return value > 0 ? "positive" : "negative";
}

export function formatAmount(amount: number | null | undefined): string {
  return `$${(amount ?? 0).toFixed(2)}`;
}

/** Members only see themselves: no team/member switch unless the viewer's scope is wider. */
export function canSwitchPerson(scope: TReportScope | undefined): boolean {
  return scope === "all" || scope === "projects";
}

/** What the first request asks for. Before the scope is known we ask for "me", which every role may. */
export function defaultTarget(scope: TReportScope | undefined): string {
  return canSwitchPerson(scope) ? "team" : "me";
}

export type TReportErrorKind = "forbidden_scope" | "forbidden_user" | "forbidden" | "invalid_range" | "other";

type TErrorLike = { code?: string; error?: string; detail?: string } | undefined | null;

export function classifyReportError(err: unknown): TReportErrorKind {
  const code = typeof err === "object" && err !== null ? (err as TErrorLike)?.code : undefined;
  if (code === "forbidden_scope" || code === "forbidden_user" || code === "forbidden" || code === "invalid_range") {
    return code;
  }
  return "other";
}

export function reportErrorMessage(err: unknown): string {
  switch (classifyReportError(err)) {
    case "forbidden_scope":
      return "You can only see your own report.";
    case "forbidden_user":
      return "You can only see reports for people in your projects.";
    case "forbidden":
      return "You don't have access to reports.";
    case "invalid_range":
      return (err as TErrorLike)?.error || "Pick a valid date range (at most 366 days).";
    default:
      return (typeof err === "object" && err !== null && (err as TErrorLike)?.error) || "Couldn't load the report.";
  }
}

const MS_PER_DAY = 86_400_000;

/** Whole days between two YYYY-MM-DD dates, inclusive of both ends. Compared as UTC so DST never shifts it. */
export function rangeDays(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.round((b - a) / MS_PER_DAY) + 1;
}

export const MAX_REPORT_DAYS = 366;

/** Client-side guard so a bad range doesn't cost a round trip. Returns a message, or null when fine. */
export function validateRange(from: string, to: string): string | null {
  if (!from || !to) return "Pick a start and an end date.";
  const days = rangeDays(from, to);
  if (days < 1) return "The end date can't be before the start date.";
  if (days > MAX_REPORT_DAYS) return `Pick a range of at most ${MAX_REPORT_DAYS} days.`;
  return null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct 7" from "2026-10-07" without going through the browser's time zone. */
export function shortDate(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  if (!m || !d) return date;
  return `${MONTHS[m - 1]} ${d}`;
}

export function dateSpan(from: string, to: string): string {
  return from === to ? shortDate(from) : `${shortDate(from)} – ${shortDate(to)}`;
}

export type TChartPoint = {
  name: string;
  logged: number;
  expected: number;
  cumulative_logged: number;
  cumulative_expected: number;
  /** 0 for weekends / non-working days: the daily bar is drawn lighter (a number: chart rows are string | number) */
  working: 0 | 1;
};

const round1 = (minutes: number) => Math.round((minutes / 60) * 10) / 10;

/** Above this many days the daily bars get too thin to read: roll up to Monday-start weeks. */
export const WEEKLY_CHART_FROM_DAYS = 63;

function mondayOf(date: string): string {
  const t = Date.parse(`${date}T00:00:00Z`);
  const dow = new Date(t).getUTCDay(); // 0 = Sunday
  const back = (dow + 6) % 7;
  return new Date(t - back * MS_PER_DAY).toISOString().slice(0, 10);
}

/**
 * Chart rows from the daily series, in hours. Long ranges roll up to Monday-start weeks.
 */
export function buildChartPoints(series: TReportSeriesPoint[]): { points: TChartPoint[]; bucket: "day" | "week" } {
  const toPoint = (name: string, rows: TReportSeriesPoint[], working: 0 | 1): TChartPoint => {
    const last = rows[rows.length - 1];
    return {
      name,
      logged: round1(rows.reduce((sum, r) => sum + r.logged_minutes, 0)),
      expected: round1(rows.reduce((sum, r) => sum + r.expected_minutes, 0)),
      cumulative_logged: round1(last.cumulative_logged_minutes),
      cumulative_expected: round1(last.cumulative_expected_minutes),
      working,
    };
  };
  if (series.length === 0) return { points: [], bucket: "day" };
  if (series.length <= WEEKLY_CHART_FROM_DAYS) {
    return { points: series.map((r) => toPoint(shortDate(r.date), [r], r.is_working_day ? 1 : 0)), bucket: "day" };
  }
  const weeks = new Map<string, TReportSeriesPoint[]>();
  for (const row of series) {
    const key = mondayOf(row.date);
    const bucket = weeks.get(key);
    if (bucket) bucket.push(row);
    else weeks.set(key, [row]);
  }
  return { points: [...weeks.entries()].map(([key, rows]) => toPoint(shortDate(key), rows, 1)), bucket: "week" };
}

/** Whether today falls inside the range, i.e. a running timer's minutes belong in this report. */
export function includesToday(from: string, to: string, today: string): boolean {
  return from <= today && today <= to;
}

/** Truncate a long task title for a table cell, on a word boundary where possible. */
export function truncateTitle(title: string, max = 80): string {
  if (title.length <= max) return title;
  const cut = title.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
