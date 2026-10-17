/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import {
  capacityToText,
  parseCapacityHours,
  parseWholeNumber,
  settingsErrorMessage,
  settingsToForm,
  toPatch,
  trackingSince,
  validateSettingsForm,
  weeklyMinutesOf,
} from "./settings-utils";
import type { TTimeSettings } from "../reports/types";

const saved: TTimeSettings = {
  id: "s1",
  workspace: "w1",
  timezone: "UTC",
  work_days: [1, 2, 3, 4, 5],
  minutes_per_day: 480,
  remind_after_minutes: 240,
  auto_stop_after_minutes: 480,
  idle_check_min_minutes: 20,
  idle_check_max_minutes: 80,
  allow_member_manual_entry: true,
  weekly_approval_enabled: false,
  billing_enabled: false,
};

describe("validateSettingsForm", () => {
  it("accepts the saved settings", () => {
    expect(validateSettingsForm(settingsToForm(saved))).toEqual({});
  });
  it("needs a work day and sane minutes per day", () => {
    const f = settingsToForm(saved);
    expect(validateSettingsForm({ ...f, work_days: [] }).work_days).toBeTruthy();
    expect(validateSettingsForm({ ...f, minutes_per_day: "0" }).minutes_per_day).toBeTruthy();
    expect(validateSettingsForm({ ...f, minutes_per_day: "1441" }).minutes_per_day).toBeTruthy();
    expect(validateSettingsForm({ ...f, minutes_per_day: "7.5" }).minutes_per_day).toBeTruthy();
    expect(validateSettingsForm({ ...f, minutes_per_day: "" }).minutes_per_day).toBeTruthy();
  });
  it("auto-stop must come after the reminder", () => {
    const f = settingsToForm(saved);
    expect(validateSettingsForm({ ...f, auto_stop_after_minutes: "240" }).auto_stop_after_minutes).toMatch(/after/);
    expect(validateSettingsForm({ ...f, remind_after_minutes: "0" }).remind_after_minutes).toBeTruthy();
    expect(validateSettingsForm({ ...f, auto_stop_after_minutes: "1500" }).auto_stop_after_minutes).toBeTruthy();
  });
  it("idle check is off (0/0) or a 5-480 window with min <= max", () => {
    const f = settingsToForm(saved);
    expect(validateSettingsForm({ ...f, idle_check_min_minutes: "0", idle_check_max_minutes: "0" })).toEqual({});
    expect(
      validateSettingsForm({ ...f, idle_check_min_minutes: "0", idle_check_max_minutes: "30" }).idle_check_min_minutes
    ).toBeTruthy();
    expect(validateSettingsForm({ ...f, idle_check_min_minutes: "4" }).idle_check_min_minutes).toBeTruthy();
    expect(validateSettingsForm({ ...f, idle_check_max_minutes: "481" }).idle_check_max_minutes).toBeTruthy();
    expect(validateSettingsForm({ ...f, idle_check_min_minutes: "90" }).idle_check_max_minutes).toMatch(/below/);
    expect(validateSettingsForm({ ...f, idle_check_min_minutes: "30", idle_check_max_minutes: "30" })).toEqual({});
  });
});

describe("toPatch", () => {
  it("is empty when nothing changed", () => {
    expect(toPatch(saved, settingsToForm(saved))).toEqual({});
  });
  it("sends only what changed, as numbers", () => {
    const f = {
      ...settingsToForm(saved),
      minutes_per_day: "450",
      billing_enabled: true,
      work_days: [5, 1, 2, 3, 4, 6],
    };
    expect(toPatch(saved, f)).toEqual({ minutes_per_day: 450, billing_enabled: true, work_days: [1, 2, 3, 4, 5, 6] });
  });
  it("sends nothing while the form is invalid", () => {
    expect(toPatch(saved, { ...settingsToForm(saved), minutes_per_day: "abc" })).toEqual({});
  });
});

describe("capacity text", () => {
  it("blank means follow the work week", () => {
    expect(parseCapacityHours("")).toBeNull();
    expect(parseCapacityHours("  ")).toBeNull();
  });
  it("parses hours to minutes within 0..168 h", () => {
    expect(parseCapacityHours("37.5")).toBe(2250);
    expect(parseCapacityHours("0")).toBe(0);
    expect(parseCapacityHours("168")).toBe(10080);
    expect(parseCapacityHours("168.5")).toBe("invalid");
    expect(parseCapacityHours("-1")).toBe("invalid");
    expect(parseCapacityHours("abc")).toBe("invalid");
    expect(parseCapacityHours("1.234")).toBe("invalid");
  });
  it("round-trips", () => {
    expect(capacityToText(2250)).toBe("37.5");
    expect(capacityToText(2400)).toBe("40");
    expect(capacityToText(null)).toBe("");
  });
  it("derives the default weekly minutes", () => {
    expect(weeklyMinutesOf([1, 2, 3, 4, 5], 480)).toBe(2400);
  });
});

describe("misc", () => {
  it("parses whole numbers only", () => {
    expect(parseWholeNumber(" 12 ")).toBe(12);
    expect(parseWholeNumber("1.5")).toBeNull();
    expect(parseWholeNumber("")).toBeNull();
  });
  it("finds the current tracking-since date, ignoring future changes", () => {
    const h = [
      { is_tracked: true, effective_from: "2026-01-01" },
      { is_tracked: false, effective_from: "2026-10-01" },
      { is_tracked: true, effective_from: "2026-12-01" },
    ];
    expect(trackingSince(h, "2026-10-16")).toBe("2026-10-01");
    expect(trackingSince([], "2026-10-16")).toBeNull();
  });
  it("reads DRF error bodies", () => {
    expect(settingsErrorMessage({ error: "Nope" })).toBe("Nope");
    expect(settingsErrorMessage({ minutes_per_day: ["Must be between 1 and 1440 minutes."] })).toMatch(/1440/);
    expect(settingsErrorMessage(["bad"])).toBe("bad");
    expect(settingsErrorMessage(undefined)).toBe("Couldn't save.");
  });
});
