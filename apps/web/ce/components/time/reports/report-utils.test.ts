/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import {
  balanceTone,
  buildChartPoints,
  canSwitchPerson,
  classifyReportError,
  dateSpan,
  defaultTarget,
  formatHm,
  formatHours,
  formatSignedHours,
  includesToday,
  rangeDays,
  reportErrorMessage,
  shortDate,
  truncateTitle,
  validateRange,
  WEEKLY_CHART_FROM_DAYS,
} from "./report-utils";
import type { TReportSeriesPoint } from "./types";

describe("hours formatting", () => {
  it("shows decimal hours to one place", () => {
    expect(formatHours(0)).toBe("0.0 h");
    expect(formatHours(450)).toBe("7.5 h");
    expect(formatHours(null)).toBe("0.0 h");
    expect(formatHours(2400)).toBe("40.0 h");
  });
  it("signs a balance and treats under 3 minutes as zero", () => {
    expect(formatSignedHours(150)).toBe("+2.5 h");
    expect(formatSignedHours(-240)).toBe("-4.0 h");
    expect(formatSignedHours(2)).toBe("0.0 h");
    expect(formatSignedHours(undefined)).toBe("0.0 h");
  });
  it("formats h:m", () => {
    expect(formatHm(0)).toBe("0m");
    expect(formatHm(45)).toBe("45m");
    expect(formatHm(120)).toBe("2h");
    expect(formatHm(135)).toBe("2h 15m");
  });
  it("tones the balance", () => {
    expect(balanceTone(60)).toBe("positive");
    expect(balanceTone(-60)).toBe("negative");
    expect(balanceTone(3)).toBe("neutral");
    expect(balanceTone(0)).toBe("neutral");
  });
});

describe("scope", () => {
  it("only wider scopes get the team/member switch", () => {
    expect(canSwitchPerson("all")).toBe(true);
    expect(canSwitchPerson("projects")).toBe(true);
    expect(canSwitchPerson("self")).toBe(false);
    expect(canSwitchPerson(undefined)).toBe(false);
  });
  it("asks for 'me' until the scope is known (a member's only legal choice)", () => {
    expect(defaultTarget(undefined)).toBe("me");
    expect(defaultTarget("self")).toBe("me");
    expect(defaultTarget("all")).toBe("team");
    expect(defaultTarget("projects")).toBe("team");
  });
});

describe("errors", () => {
  it("classifies the API codes", () => {
    expect(classifyReportError({ code: "forbidden_scope" })).toBe("forbidden_scope");
    expect(classifyReportError({ code: "forbidden_user" })).toBe("forbidden_user");
    expect(classifyReportError({ code: "invalid_range" })).toBe("invalid_range");
    expect(classifyReportError({ error: "boom" })).toBe("other");
    expect(classifyReportError(undefined)).toBe("other");
    expect(classifyReportError("x")).toBe("other");
  });
  it("words each one in plain text", () => {
    expect(reportErrorMessage({ code: "forbidden_scope" })).toMatch(/own report/);
    expect(reportErrorMessage({ code: "forbidden_user" })).toMatch(/people in your projects/);
    expect(reportErrorMessage({ code: "invalid_range", error: "from must be before to" })).toBe(
      "from must be before to"
    );
    expect(reportErrorMessage(undefined)).toBe("Couldn't load the report.");
  });
});

describe("range", () => {
  it("counts inclusive days", () => {
    expect(rangeDays("2026-10-01", "2026-10-07")).toBe(7);
    expect(rangeDays("2026-10-01", "2026-10-01")).toBe(1);
    expect(rangeDays("2026-03-01", "2026-03-31")).toBe(31);
    expect(rangeDays("bad", "2026-10-01")).toBe(0);
  });
  it("validates before asking the server", () => {
    expect(validateRange("2026-10-01", "2026-10-07")).toBeNull();
    expect(validateRange("2026-10-07", "2026-10-01")).toMatch(/before/);
    expect(validateRange("", "2026-10-01")).toMatch(/start and an end/);
    expect(validateRange("2025-01-01", "2026-10-01")).toMatch(/366/);
    expect(validateRange("2026-01-01", "2026-12-31")).toBeNull(); // 365 days
  });
  it("formats dates without time-zone drift", () => {
    expect(shortDate("2026-10-07")).toBe("Oct 7");
    expect(dateSpan("2026-10-01", "2026-10-07")).toBe("Oct 1 – Oct 7");
    expect(dateSpan("2026-10-01", "2026-10-01")).toBe("Oct 1");
    expect(includesToday("2026-10-01", "2026-10-07", "2026-10-07")).toBe(true);
    expect(includesToday("2026-10-01", "2026-10-07", "2026-10-08")).toBe(false);
  });
});

const day = (date: string, logged: number, expected: number, cumL: number, cumE: number, working = true) =>
  ({
    date,
    logged_minutes: logged,
    expected_minutes: expected,
    is_working_day: working,
    is_future: false,
    cumulative_logged_minutes: cumL,
    cumulative_expected_minutes: cumE,
  }) satisfies TReportSeriesPoint;

describe("chart points", () => {
  it("converts a short range day by day, in hours", () => {
    const { points, bucket } = buildChartPoints([
      day("2026-10-05", 480, 480, 480, 480),
      day("2026-10-10", 90, 0, 570, 480, false),
    ]);
    expect(bucket).toBe("day");
    expect(points).toEqual([
      { name: "Oct 5", logged: 8, expected: 8, cumulative_logged: 8, cumulative_expected: 8, working: 1 },
      { name: "Oct 10", logged: 1.5, expected: 0, cumulative_logged: 9.5, cumulative_expected: 8, working: 0 },
    ]);
  });
  it("rolls long ranges up into Monday weeks and keeps the running total", () => {
    const rows: TReportSeriesPoint[] = [];
    let cumL = 0;
    let cumE = 0;
    // 2026-08-03 is a Monday; 70 days = 10 whole weeks
    for (let i = 0; i < WEEKLY_CHART_FROM_DAYS + 7; i++) {
      const d = new Date(Date.UTC(2026, 7, 3) + i * 86_400_000).toISOString().slice(0, 10);
      cumL += 60;
      cumE += 30;
      rows.push(day(d, 60, 30, cumL, cumE));
    }
    const { points, bucket } = buildChartPoints(rows);
    expect(bucket).toBe("week");
    expect(points).toHaveLength(10);
    expect(points[0].name).toBe("Aug 3");
    expect(points[0].logged).toBe(7);
    expect(points[0].expected).toBe(3.5);
    expect(points[9].cumulative_logged).toBe(70);
  });
  it("is empty for no data", () => {
    expect(buildChartPoints([]).points).toEqual([]);
  });
});

describe("truncateTitle", () => {
  it("leaves short titles alone and cuts long ones with an ellipsis", () => {
    expect(truncateTitle("Fix login")).toBe("Fix login");
    const long = "word ".repeat(40).trim();
    const out = truncateTitle(long, 30);
    expect(out.length).toBeLessThanOrEqual(30);
    expect(out.endsWith("…")).toBe(true);
  });
});
