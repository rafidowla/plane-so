/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122)
import { describe, expect, it } from "vitest";
import {
  calendarSelectValue,
  coercePortion,
  dayOffText,
  dayOffTooltip,
  defaultHolidayDate,
  defaultOptionLabel,
  describeDaysOff,
  daysOffTotal,
  formatDaysOff,
  halfDayAllowed,
  holidayErrorMessage,
  isIsoDate,
  leaveSpan,
  offHint,
  portionLabel,
  todayIso,
  validateCalendarName,
  validateHolidayForm,
  validateLeaveForm,
  yearOptions,
} from "./holidays-utils";
import type { TDayOff, THolidayCalendar } from "./types";

const off = (over: Partial<TDayOff> = {}): TDayOff => ({
  date: "2026-10-07",
  kind: "holiday",
  portion: "full",
  label: "Diwali",
  ...over,
});

const calendars: THolidayCalendar[] = [
  { id: "a", name: "UK", is_default: true, member_count: 3 },
  { id: "b", name: "India", is_default: false, member_count: 1 },
];

describe("holidayErrorMessage", () => {
  it("maps the known codes to friendly wording", () => {
    for (const code of [
      "duplicate_name",
      "default_required",
      "default_calendar",
      "duplicate_date",
      "invalid_range",
      "half_day_range",
      "overlap",
      "not_a_member",
    ]) {
      const message = holidayErrorMessage({ code, error: "raw server text" });
      expect(message).not.toBe("raw server text");
      expect(message.length).toBeGreaterThan(10);
    }
  });

  it("falls back to the server text, then to DRF-style bodies, then to the fallback", () => {
    expect(holidayErrorMessage({ code: "something_new", error: "Nope." })).toBe("Nope.");
    expect(holidayErrorMessage({ name: ["Required."] })).toBe("Required.");
    expect(holidayErrorMessage("plain")).toBe("plain");
    expect(holidayErrorMessage(undefined, "Couldn't save.")).toBe("Couldn't save.");
    expect(holidayErrorMessage({}, "x")).toBe("x");
  });

  it("can prefer the server's own sentence over the canned one", () => {
    const body = { code: "default_calendar", error: "Make another list the default first." };
    expect(holidayErrorMessage(body, "x", true)).toBe("Make another list the default first.");
    expect(holidayErrorMessage({ code: "default_calendar" }, "x", true)).toContain("default holiday list");
  });
});

describe("dates and portions", () => {
  it("recognises real ISO dates only", () => {
    expect(isIsoDate("2026-10-07")).toBe(true);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("07/10/2026")).toBe(false);
    expect(isIsoDate("")).toBe(false);
  });

  it("allows a half day only on a single day", () => {
    expect(halfDayAllowed("2026-10-07", "2026-10-07")).toBe(true);
    expect(halfDayAllowed("2026-10-07", "2026-10-08")).toBe(false);
    expect(halfDayAllowed("", "")).toBe(false);
  });

  it("drops a half day back to full when the range grows", () => {
    expect(coercePortion("half", "2026-10-07", "2026-10-09")).toBe("full");
    expect(coercePortion("half", "2026-10-07", "2026-10-07")).toBe("half");
    expect(coercePortion("full", "2026-10-07", "2026-10-09")).toBe("full");
  });

  it("labels portions", () => {
    expect(portionLabel("full")).toBe("Full day");
    expect(portionLabel("half")).toBe("Half day");
  });
});

describe("validateLeaveForm", () => {
  const ok = { user_id: "u1", start_date: "2026-10-07", end_date: "2026-10-09", portion: "full" as const, note: "" };

  it("accepts a valid range", () => {
    expect(validateLeaveForm(ok)).toEqual({});
    expect(validateLeaveForm({ ...ok, end_date: ok.start_date, portion: "half" })).toEqual({});
  });

  it("requires a person and both dates", () => {
    const errors = validateLeaveForm({ ...ok, user_id: "", start_date: "", end_date: "" });
    expect(errors.user_id).toBeTruthy();
    expect(errors.start_date).toBeTruthy();
    expect(errors.end_date).toBeTruthy();
  });

  it("refuses an end before the start", () => {
    expect(validateLeaveForm({ ...ok, end_date: "2026-10-01" }).end_date).toBeTruthy();
  });

  it("refuses a half day over several days", () => {
    const errors = validateLeaveForm({ ...ok, portion: "half" });
    expect(errors.portion).toBeTruthy();
    expect(errors.end_date).toBeUndefined();
  });
});

describe("validateHolidayForm / validateCalendarName", () => {
  it("needs a date and a name", () => {
    expect(validateHolidayForm({ date: "2026-12-25", name: "Christmas", portion: "full" })).toEqual({});
    const errors = validateHolidayForm({ date: "", name: "  ", portion: "full" });
    expect(errors.date).toBeTruthy();
    expect(errors.name).toBeTruthy();
  });

  it("rejects blank and duplicate list names (case-insensitive), ignoring the list being renamed", () => {
    expect(validateCalendarName(" ", calendars)).toBeTruthy();
    expect(validateCalendarName("uk", calendars)).toBeTruthy();
    expect(validateCalendarName("uk", calendars, "a")).toBeNull();
    expect(validateCalendarName("Canada", calendars)).toBeNull();
  });
});

describe("year picker and defaults", () => {
  it("builds a window around the current year and keeps a selected year outside it", () => {
    expect(yearOptions(2026)).toEqual([2024, 2025, 2026, 2027, 2028, 2029]);
    expect(yearOptions(2026, 2035)).toContain(2035);
    expect(yearOptions(2026, 2035).at(-1)).toBe(2035);
  });

  it("defaults the holiday date to today in the current year, else 1 January", () => {
    expect(defaultHolidayDate(2026, "2026-10-17")).toBe("2026-10-17");
    expect(defaultHolidayDate(2027, "2026-10-17")).toBe("2027-01-01");
  });
});

describe("todayIso", () => {
  it("pads month and day from the local date", () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
    expect(todayIso(new Date(2026, 9, 17))).toBe("2026-10-17");
  });
});

describe("member holiday list select", () => {
  it("names the default list in the first option", () => {
    expect(defaultOptionLabel(calendars)).toBe("Workspace default (UK)");
    expect(defaultOptionLabel([])).toBe("Workspace default (none yet)");
  });

  it("falls back to the default option for null or a stale id", () => {
    expect(calendarSelectValue(null, calendars)).toBe("");
    expect(calendarSelectValue("b", calendars)).toBe("b");
    expect(calendarSelectValue("gone", calendars)).toBe("");
  });
});

describe("days off display", () => {
  it("words the day marker by kind and portion", () => {
    expect(dayOffText(off())).toBe("Holiday");
    expect(dayOffText(off({ kind: "leave", label: "Leave" }))).toBe("Leave");
    expect(dayOffText(off({ portion: "half" }))).toBe("½ day holiday");
    expect(dayOffText(off({ kind: "leave", portion: "half" }))).toBe("½ day leave");
    expect(dayOffText(off({ kind: "both", portion: "full" }))).toBe("Holiday + leave");
    expect(dayOffText(off({ kind: "both", portion: "half" }))).toBe("½ day off");
  });

  it("adds the portion to the tooltip", () => {
    expect(dayOffTooltip(off())).toBe("Diwali (full day)");
    expect(dayOffTooltip(off({ portion: "half" }))).toBe("Diwali (half day)");
  });

  it("counts half days as 0.5", () => {
    expect(daysOffTotal(undefined)).toBe(0);
    expect(daysOffTotal([off(), off({ portion: "half" })])).toBe(1.5);
    expect(formatDaysOff([])).toBe("–");
    expect(formatDaysOff([off(), off({ date: "2026-10-08" })])).toBe("2");
    expect(formatDaysOff([off(), off({ portion: "half" })])).toBe("1.5");
  });

  it("lists dates and labels for the tooltip", () => {
    expect(
      describeDaysOff([off(), off({ date: "2026-10-09", kind: "leave", portion: "half", label: "Dentist" })])
    ).toBe("Oct 7: Diwali (full day)\nOct 9: Dentist (half day)");
    expect(describeDaysOff([])).toBe("");
  });

  it("hints how much expected time was taken off", () => {
    expect(offHint(0)).toBeNull();
    expect(offHint(undefined)).toBeNull();
    expect(offHint(480)).toBe("incl. 8.0 h off");
    expect(offHint(240)).toBe("incl. 4.0 h off");
  });

  it("formats a leave span", () => {
    expect(leaveSpan("2026-10-07", "2026-10-07")).toBe("Oct 7");
    expect(leaveSpan("2026-10-07", "2026-10-09")).toBe("Oct 7 – Oct 9");
  });
});
