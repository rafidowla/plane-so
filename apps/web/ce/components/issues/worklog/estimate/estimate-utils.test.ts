/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6
import { describe, expect, it } from "vitest";
import {
  describeEstimateError,
  estimatePercent,
  estimateToInput,
  formatRevisionTime,
  isOverEstimate,
  parseEstimateInput,
} from "./estimate-utils";
import {
  handleEstimateRequired,
  isEstimateFieldMounted,
  isEstimateRequiredError,
  registerEstimateField,
  subscribeEstimateRequired,
} from "./estimate-required";

const minutes = (text: string) => {
  const r = parseEstimateInput(text);
  if (!r.ok) throw new Error(r.message);
  return r.minutes;
};

describe("parseEstimateInput", () => {
  it("reads a bare number as hours", () => {
    expect(minutes("2")).toBe(120);
    expect(minutes("1.5")).toBe(90);
    expect(minutes("0,5")).toBe(30);
    expect(minutes(" 3 ")).toBe(180);
  });

  it("reads hours and minutes with units", () => {
    expect(minutes("2h")).toBe(120);
    expect(minutes("2h 30m")).toBe(150);
    expect(minutes("2h30")).toBe(150);
    expect(minutes("1.5 hours")).toBe(90);
    expect(minutes("90m")).toBe(90);
    expect(minutes("45 min")).toBe(45);
    expect(minutes("2H 5M")).toBe(125);
  });

  it("reads h:mm", () => {
    expect(minutes("2:30")).toBe(150);
    expect(minutes("0:05")).toBe(5);
  });

  it("refuses empty, nonsense, zero and too large values with a message", () => {
    for (const bad of ["", "  ", "abc", "2x", "h", "-1", "0", "0m", "1001", "5:75", "1000h 1m"]) {
      const r = parseEstimateInput(bad);
      expect(r.ok, bad).toBe(false);
      if (!r.ok) expect(r.message.length).toBeGreaterThan(5);
    }
    expect(minutes("1000")).toBe(60_000);
  });
});

describe("estimate display helpers", () => {
  it("formats minutes back for the edit box", () => {
    expect(estimateToInput(150)).toBe("2h 30m");
    expect(estimateToInput(null)).toBe("");
    expect(parseEstimateInput(estimateToInput(150))).toEqual({ ok: true, minutes: 150 });
  });

  it("is over only when logged is strictly above the estimate", () => {
    expect(isOverEstimate(121, 120)).toBe(true);
    expect(isOverEstimate(120, 120)).toBe(false);
    expect(isOverEstimate(500, null)).toBe(false);
    expect(estimatePercent(180, 120)).toBe(150);
    expect(estimatePercent(10, null)).toBe(0);
  });

  it("formats revision time in the given zone and survives bad input", () => {
    expect(formatRevisionTime("2026-10-16T19:04:00Z", "America/Toronto")).toContain("3:04");
    expect(formatRevisionTime("nope", "UTC")).toBe("nope");
    expect(formatRevisionTime("2026-10-16T19:04:00Z", "Not/AZone")).not.toBe("");
  });

  it("words error codes in plain language", () => {
    expect(describeEstimateError({ code: "estimates_off" })).toMatch(/turned off/);
    expect(describeEstimateError({ code: "invalid_minutes", error: "Estimate must be 1-60000 minutes." })).toMatch(
      /60000/
    );
    expect(describeEstimateError(undefined)).toMatch(/Try again/);
  });
});

describe("estimate-required bus", () => {
  it("recognises the error and tells subscribers, filling the item from the payload", () => {
    const seen: unknown[] = [];
    const off = subscribeEstimateRequired((d) => seen.push(d));
    expect(isEstimateRequiredError({ code: "estimate_required" })).toBe(true);
    expect(isEstimateRequiredError({ code: "overlap" })).toBe(false);
    expect(isEstimateRequiredError(undefined)).toBe(false);

    expect(handleEstimateRequired({ code: "overlap" }, { issueId: "a" })).toBe(false);
    expect(seen).toHaveLength(0);
    expect(handleEstimateRequired({ code: "estimate_required", issue_id: "from-payload" }, {})).toBe(true);
    expect(handleEstimateRequired({ code: "estimate_required", issue_id: "x" }, { issueId: "mine" })).toBe(true);
    expect(seen).toEqual([{ issueId: "from-payload" }, { issueId: "mine" }]);
    off();
    handleEstimateRequired({ code: "estimate_required" }, {});
    expect(seen).toHaveLength(2);
  });

  it("counts mounted fields per work item", () => {
    expect(isEstimateFieldMounted("i1")).toBe(false);
    const a = registerEstimateField("i1");
    const b = registerEstimateField("i1");
    expect(isEstimateFieldMounted("i1")).toBe(true);
    a();
    expect(isEstimateFieldMounted("i1")).toBe(true);
    b();
    expect(isEstimateFieldMounted("i1")).toBe(false);
    expect(isEstimateFieldMounted(null)).toBe(false);
  });
});
