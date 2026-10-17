/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import {
  addDays,
  describeBalance,
  entryActionFor,
  formatCell,
  formatDayLong,
  formatDayShort,
  formatSpan,
  formatWeekRange,
  groupBadge,
  isPastDay,
  mondayOf,
  paceOf,
  shiftWeek,
} from "./timesheet-utils";

describe("week math", () => {
  it("snaps any day to its Monday", () => {
    expect(mondayOf("2026-06-22")).toBe("2026-06-22"); // Monday
    expect(mondayOf("2026-06-24")).toBe("2026-06-22"); // Wednesday
    expect(mondayOf("2026-06-28")).toBe("2026-06-22"); // Sunday belongs to the week before Monday 29th
    expect(mondayOf("2026-06-29")).toBe("2026-06-29");
  });

  it("moves by whole weeks across month and year ends", () => {
    expect(shiftWeek("2026-06-22", 1)).toBe("2026-06-29");
    expect(shiftWeek("2026-06-22", -1)).toBe("2026-06-15");
    expect(shiftWeek("2026-12-28", 1)).toBe("2027-01-04");
    expect(shiftWeek("2024-02-26", 1)).toBe("2024-03-04"); // leap year
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("leaves something that is not a date alone", () => {
    expect(addDays("nope", 3)).toBe("nope");
    expect(mondayOf("")).toBe("");
  });
});

describe("wording", () => {
  it("formats the week range, with the year on both ends only across New Year", () => {
    expect(formatWeekRange("2026-06-22", "2026-06-28")).toBe("22 Jun – 28 Jun 2026");
    expect(formatWeekRange("2026-06-29", "2026-07-05")).toBe("29 Jun – 5 Jul 2026");
    expect(formatWeekRange("2026-12-28", "2027-01-03")).toBe("28 Dec 2026 – 3 Jan 2027");
  });

  it("names days", () => {
    expect(formatDayShort("2026-06-22")).toBe("Mon 22");
    expect(formatDayShort("2026-06-28")).toBe("Sun 28");
    expect(formatDayLong("2026-06-24")).toBe("Wednesday 24 June");
  });

  it("shows no time as a dash and the rest as hours and minutes", () => {
    expect(formatCell(0)).toBe("–");
    expect(formatCell(45)).toBe("45m");
    expect(formatCell(90)).toBe("1h 30m");
  });

  it("shows a span in the workspace zone, and nothing for manual entries", () => {
    // 07:00 UTC is 13:00 in Dhaka (UTC+6)
    expect(formatSpan("2026-06-23T07:00:00Z", "2026-06-23T09:30:00Z", "Asia/Dhaka")).toBe("13:00 – 15:30");
    expect(formatSpan(null, null, "Asia/Dhaka")).toBe("");
    expect(formatSpan("2026-06-23T07:00:00Z", null, "Asia/Dhaka")).toBe("");
  });

  it("only badges a task that has more than one entry", () => {
    expect(groupBadge(1)).toBeNull();
    expect(groupBadge(2)).toBe("2");
  });
});

describe("pace", () => {
  it("compares logged with expected", () => {
    expect(paceOf(0, 0)).toBe("none");
    expect(paceOf(60, 0)).toBe("over"); // weekend work
    expect(paceOf(100, 480)).toBe("under");
    expect(paceOf(480, 480)).toBe("met");
    expect(paceOf(500, 480)).toBe("over");
  });

  it("describes the week so far", () => {
    expect(describeBalance(0, 0)).toBe("");
    expect(describeBalance(480, 480)).toBe("on target");
    expect(describeBalance(330, 480)).toBe("2h 30m under");
    expect(describeBalance(540, 480)).toBe("1h over");
  });
});

describe("what an entry offers", () => {
  const mine = { is_self: true, viewer: { is_workspace_admin: false, can_add_time: false } };
  const admin = { is_self: true, viewer: { is_workspace_admin: true, can_add_time: true } };
  const free = { is_locked: false, pending_request_id: null };

  it("lets members request a change, and admins edit directly", () => {
    expect(entryActionFor(mine, free)).toEqual({ kind: "request_change" });
    expect(entryActionFor(admin, free)).toEqual({ kind: "edit" });
  });

  it("blocks a locked or already-pending entry for members", () => {
    expect(entryActionFor(mine, { ...free, is_locked: true })).toEqual({
      kind: "none",
      reason: "Approved and locked",
    });
    expect(entryActionFor(mine, { ...free, pending_request_id: "r1" })).toEqual({
      kind: "none",
      reason: "Change pending",
    });
  });

  it("also blocks admins on a locked or pending entry", () => {
    expect(entryActionFor(admin, { ...free, is_locked: true }).kind).toBe("none");
    expect(entryActionFor(admin, { ...free, pending_request_id: "r1" }).kind).toBe("none");
  });

  it("is read-only when looking at someone else", () => {
    expect(entryActionFor({ ...mine, is_self: false }, free)).toEqual({ kind: "none", reason: "" });
    expect(entryActionFor({ ...admin, is_self: false }, free)).toEqual({ kind: "none", reason: "" });
  });
});

describe("days", () => {
  it("compares calendar-day strings", () => {
    expect(isPastDay("2026-06-23", "2026-06-24")).toBe(true);
    expect(isPastDay("2026-06-24", "2026-06-24")).toBe(false);
    expect(isPastDay("2026-06-25", "2026-06-24")).toBe(false);
  });
});
