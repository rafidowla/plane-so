/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — pure helpers for the Settings tab (relative imports only: unit-tested by vitest).
// Validation mirrors TimeTrackingSettingSerializer so the form can say "no" before the server does.
import type { TTimeSettings, TTimeSettingsPatch } from "../reports/types";

export const WEEKDAYS: { iso: number; short: string; long: string }[] = [
  { iso: 1, short: "Mon", long: "Monday" },
  { iso: 2, short: "Tue", long: "Tuesday" },
  { iso: 3, short: "Wed", long: "Wednesday" },
  { iso: 4, short: "Thu", long: "Thursday" },
  { iso: 5, short: "Fri", long: "Friday" },
  { iso: 6, short: "Sat", long: "Saturday" },
  { iso: 7, short: "Sun", long: "Sunday" },
];

/** Distinct ISO weekdays in Monday-first order. */
export function normalizeDays(days: number[]): number[] {
  return WEEKDAYS.map((d) => d.iso).filter((iso) => days.includes(iso));
}

/** What the form edits. Numbers are text while typing; `toPatch` parses them. */
export type TSettingsForm = {
  work_days: number[];
  minutes_per_day: string;
  remind_after_minutes: string;
  auto_stop_after_minutes: string;
  idle_check_min_minutes: string;
  idle_check_max_minutes: string;
  allow_member_manual_entry: boolean;
  weekly_approval_enabled: boolean;
  billing_enabled: boolean;
};

export type TSettingsErrors = Partial<Record<keyof TSettingsForm, string>>;

export function settingsToForm(s: TTimeSettings): TSettingsForm {
  return {
    work_days: normalizeDays(s.work_days),
    minutes_per_day: String(s.minutes_per_day),
    remind_after_minutes: String(s.remind_after_minutes),
    auto_stop_after_minutes: String(s.auto_stop_after_minutes),
    idle_check_min_minutes: String(s.idle_check_min_minutes),
    idle_check_max_minutes: String(s.idle_check_max_minutes),
    allow_member_manual_entry: s.allow_member_manual_entry,
    weekly_approval_enabled: s.weekly_approval_enabled,
    billing_enabled: s.billing_enabled ?? false,
  };
}

/** A whole number from a text box, or null when it isn't one. */
export function parseWholeNumber(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  return Number(trimmed);
}

export function validateSettingsForm(form: TSettingsForm): TSettingsErrors {
  const errors: TSettingsErrors = {};
  if (form.work_days.length === 0) errors.work_days = "Pick at least one work day.";

  const perDay = parseWholeNumber(form.minutes_per_day);
  if (perDay === null || perDay < 1 || perDay > 1440) errors.minutes_per_day = "Enter 1 to 1440 minutes.";

  const remind = parseWholeNumber(form.remind_after_minutes);
  const autoStop = parseWholeNumber(form.auto_stop_after_minutes);
  if (remind === null || remind < 1 || remind > 1440) errors.remind_after_minutes = "Enter 1 to 1440 minutes.";
  if (autoStop === null || autoStop < 1 || autoStop > 1440) {
    errors.auto_stop_after_minutes = "Enter 1 to 1440 minutes.";
  } else if (remind !== null && !errors.remind_after_minutes && autoStop <= remind) {
    errors.auto_stop_after_minutes = "Auto-stop must come after the reminder.";
  }

  const idleMin = parseWholeNumber(form.idle_check_min_minutes);
  const idleMax = parseWholeNumber(form.idle_check_max_minutes);
  if (idleMin === null) errors.idle_check_min_minutes = "Enter a number of minutes (0 to turn off).";
  if (idleMax === null) errors.idle_check_max_minutes = "Enter a number of minutes (0 to turn off).";
  if (idleMin !== null && idleMax !== null && !(idleMin === 0 && idleMax === 0)) {
    if (idleMin < 5 || idleMin > 480) errors.idle_check_min_minutes = "0 (off, with the maximum also 0) or 5 to 480.";
    else if (idleMax < 5 || idleMax > 480)
      errors.idle_check_max_minutes = "0 (off, with the minimum also 0) or 5 to 480.";
    else if (idleMin > idleMax) errors.idle_check_max_minutes = "The maximum must not be below the minimum.";
  }
  return errors;
}

/** Only the fields that differ from what's saved. Empty when nothing changed or the form is invalid. */
export function toPatch(saved: TTimeSettings, form: TSettingsForm): TTimeSettingsPatch {
  if (Object.keys(validateSettingsForm(form)).length > 0) return {};
  const next = {
    work_days: normalizeDays(form.work_days),
    minutes_per_day: Number(form.minutes_per_day),
    remind_after_minutes: Number(form.remind_after_minutes),
    auto_stop_after_minutes: Number(form.auto_stop_after_minutes),
    idle_check_min_minutes: Number(form.idle_check_min_minutes),
    idle_check_max_minutes: Number(form.idle_check_max_minutes),
    allow_member_manual_entry: form.allow_member_manual_entry,
    weekly_approval_enabled: form.weekly_approval_enabled,
    billing_enabled: form.billing_enabled,
  };
  const before = settingsToForm(saved);
  const patch: Record<string, unknown> = {};
  const same = (key: keyof typeof next) =>
    key === "work_days"
      ? before.work_days.join(",") === next.work_days.join(",")
      : (key === "allow_member_manual_entry" || key === "weekly_approval_enabled" || key === "billing_enabled"
          ? before[key]
          : Number(before[key])) === next[key];
  (Object.keys(next) as (keyof typeof next)[]).forEach((key) => {
    if (!same(key)) patch[key] = next[key];
  });
  return patch as TTimeSettingsPatch;
}

/** Weekly hours implied by the work week, for the "default" hint: days x minutes per day. */
export function weeklyMinutesOf(workDays: number[], minutesPerDay: number): number {
  return workDays.length * minutesPerDay;
}

/** "37.5" -> 2250 minutes. null (blank) means "follow the work week"; "invalid" when out of 0..168 h. */
export function parseCapacityHours(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return "invalid";
  const minutes = Math.round(Number(trimmed) * 60);
  return minutes >= 0 && minutes <= 10080 ? minutes : "invalid";
}

/** 2250 -> "37.5"; null -> "" (blank = follows the work week). */
export function capacityToText(minutes: number | null): string {
  if (minutes === null) return "";
  const hours = minutes / 60;
  return Number.isInteger(hours) ? String(hours) : String(Math.round(hours * 100) / 100);
}

/** Latest "since" for a member's tracking switch, from the history (newest effective_from not in the future). */
export function trackingSince(
  history: { is_tracked: boolean; effective_from: string }[],
  today: string
): string | null {
  let latest: string | null = null;
  for (const h of history) {
    if (h.effective_from <= today && (latest === null || h.effective_from > latest)) latest = h.effective_from;
  }
  return latest;
}

/** One readable sentence out of a DRF error body: {error}, {field: [msg]}, [msg] or a string. */
export function settingsErrorMessage(err: unknown, fallback = "Couldn't save."): string {
  if (typeof err === "string") return err;
  if (Array.isArray(err)) return typeof err[0] === "string" ? err[0] : fallback;
  if (typeof err === "object" && err !== null) {
    const body = err as Record<string, unknown>;
    if (typeof body.error === "string") return body.error;
    for (const value of Object.values(body)) {
      if (typeof value === "string") return value;
      if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    }
  }
  return fallback;
}
