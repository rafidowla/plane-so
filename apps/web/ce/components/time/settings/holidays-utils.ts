/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — pure helpers for the Holidays & leave settings, the
// timesheet day markers and the report "days off" column (relative imports only: unit-tested by vitest).
import { dateSpan, formatHours, shortDate } from "../reports/report-utils";
import type { TDayOff, TDayPortion, THolidayCalendar, THolidayLeaveErrorCode } from "./types";

export const PORTION_OPTIONS: { value: TDayPortion; label: string }[] = [
  { value: "full", label: "Full day" },
  { value: "half", label: "Half day" },
];

export function portionLabel(portion: TDayPortion): string {
  return portion === "half" ? "Half day" : "Full day";
}

/** Friendly wording for the codes the holiday/leave endpoints answer with; anything else falls back to the server text. */
const ERROR_MESSAGES: Record<THolidayLeaveErrorCode, string> = {
  duplicate_name: "A holiday list with that name already exists.",
  default_required: "There must always be a default holiday list. Make another list the default instead.",
  default_calendar:
    "The default holiday list can't be deleted while other lists exist. Make another list the default first.",
  duplicate_date: "That list already has a holiday on this date.",
  invalid_range: "The end date can't be before the start date.",
  half_day_range: "A half day can only cover a single day. Use a full day for a longer stretch.",
  overlap: "This person already has leave on some of these days. Edit or remove the existing leave first.",
  not_a_member: "That person isn't an active member of this workspace.",
  forbidden: "Only workspace admins can change holidays and leave.",
};

type TErrorBody = { code?: string; error?: string; detail?: string; message?: string };

/** One readable sentence from an error body `{ error, code }` (DRF `{field: [msg]}` and plain strings also work). */
export function holidayErrorMessage(err: unknown, fallback = "Couldn't save.", preferServer = false): string {
  if (typeof err === "string") return err || fallback;
  if (typeof err !== "object" || err === null) return fallback;
  const body = err as TErrorBody;
  const serverText = [body.error, body.message].find((t) => typeof t === "string" && t);
  if (preferServer && serverText) return serverText;
  if (body.code && body.code in ERROR_MESSAGES) return ERROR_MESSAGES[body.code as THolidayLeaveErrorCode];
  for (const text of [body.error, body.message, body.detail]) {
    if (typeof text === "string" && text) return text;
  }
  for (const value of Object.values(err as Record<string, unknown>)) {
    if (typeof value === "string" && value) return value;
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  }
  return fallback;
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** True for a real calendar date written as YYYY-MM-DD. */
export function isIsoDate(text: string): boolean {
  if (!DATE_RE.test(text)) return false;
  const t = Date.parse(`${text}T00:00:00Z`);
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === text;
}

/** A half day only makes sense for a single day (the server refuses `half_day_range`). */
export function halfDayAllowed(start: string, end: string): boolean {
  return Boolean(start) && start === end;
}

/** Keep the chosen portion valid for the dates: a multi-day range can only be a full day. */
export function coercePortion(portion: TDayPortion, start: string, end: string): TDayPortion {
  return portion === "half" && !halfDayAllowed(start, end) ? "full" : portion;
}

export type TLeaveForm = { user_id: string; start_date: string; end_date: string; portion: TDayPortion; note: string };
export type TLeaveFormErrors = Partial<Record<"user_id" | "start_date" | "end_date" | "portion", string>>;

export function validateLeaveForm(form: TLeaveForm): TLeaveFormErrors {
  const errors: TLeaveFormErrors = {};
  if (!form.user_id) errors.user_id = "Pick a person.";
  if (!isIsoDate(form.start_date)) errors.start_date = "Pick a start date.";
  if (!isIsoDate(form.end_date)) errors.end_date = "Pick an end date.";
  if (!errors.start_date && !errors.end_date) {
    if (form.end_date < form.start_date) errors.end_date = "The end date can't be before the start date.";
    else if (form.portion === "half" && !halfDayAllowed(form.start_date, form.end_date)) {
      errors.portion = "A half day can only cover a single day.";
    }
  }
  return errors;
}

export type THolidayForm = { date: string; name: string; portion: TDayPortion };
export type THolidayFormErrors = Partial<Record<"date" | "name", string>>;

export function validateHolidayForm(form: THolidayForm): THolidayFormErrors {
  const errors: THolidayFormErrors = {};
  if (!isIsoDate(form.date)) errors.date = "Pick a date.";
  if (!form.name.trim()) errors.name = "Give the holiday a name.";
  else if (form.name.trim().length > 255) errors.name = "Keep the name under 255 characters.";
  return errors;
}

export function validateCalendarName(name: string, existing: THolidayCalendar[], ignoreId?: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Give the list a name.";
  if (trimmed.length > 255) return "Keep the name under 255 characters.";
  const clash = existing.some((c) => c.id !== ignoreId && c.name.trim().toLowerCase() === trimmed.toLowerCase());
  return clash ? "A holiday list with that name already exists." : null;
}

/** Years offered by the holiday year picker: a window around the current year (and the selected one). */
export function yearOptions(currentYear: number, selected?: number, back = 2, forward = 3): number[] {
  const years: number[] = [];
  for (let y = currentYear - back; y <= currentYear + forward; y += 1) years.push(y);
  if (selected && !years.includes(selected)) {
    if (selected < years[0]) years.unshift(selected);
    else years.push(selected);
  }
  return years;
}

export function yearOfDate(date: string): number {
  return Number(date.slice(0, 4));
}

/** First day of a year, a sensible default for the "add holiday" date box. */
export function defaultHolidayDate(year: number, today: string): string {
  return yearOfDate(today) === year ? today : `${year}-01-01`;
}

/** "Workspace default (UK)" / "Workspace default (none yet)". */
export function defaultOptionLabel(calendars: THolidayCalendar[]): string {
  const def = calendars.find((c) => c.is_default);
  return def ? `Workspace default (${def.name})` : "Workspace default (none yet)";
}

/** Select value for a member: "" means "use the default". A stale id (list deleted) also falls back to "". */
export function calendarSelectValue(calendarId: string | null, calendars: THolidayCalendar[]): string {
  return calendarId && calendars.some((c) => c.id === calendarId) ? calendarId : "";
}

// ---- Days off (timesheet markers, report column) ----

/** Short marker for a day header: "Holiday", "Leave", "½ day off"... */
export function dayOffText(off: TDayOff): string {
  if (off.portion === "half") {
    if (off.kind === "holiday") return "½ day holiday";
    if (off.kind === "leave") return "½ day leave";
    return "½ day off";
  }
  if (off.kind === "holiday") return "Holiday";
  if (off.kind === "leave") return "Leave";
  return "Holiday + leave";
}

/** Hover text for the marker: the label the server built, plus the portion. */
export function dayOffTooltip(off: TDayOff): string {
  return `${off.label} (${off.portion === "half" ? "half day" : "full day"})`;
}

/** Days off as a number: a full day is 1, a half day 0.5. */
export function daysOffTotal(daysOff: TDayOff[] | undefined): number {
  return (daysOff ?? []).reduce((sum, d) => sum + (d.portion === "half" ? 0.5 : 1), 0);
}

/** "–" when none, otherwise "2" or "1.5". */
export function formatDaysOff(daysOff: TDayOff[] | undefined): string {
  const total = daysOffTotal(daysOff);
  if (total === 0) return "–";
  return Number.isInteger(total) ? String(total) : total.toFixed(1);
}

/** One line per day for a tooltip: "Oct 7: Diwali (half day)". */
export function describeDaysOff(daysOff: TDayOff[] | undefined): string {
  return (daysOff ?? []).map((d) => `${shortDate(d.date)}: ${dayOffTooltip(d)}`).join("\n");
}

/** Hint under the Expected tile: "incl. 8.0 h off", or null when nothing was taken off. */
export function offHint(offMinutes: number | null | undefined): string | null {
  return offMinutes && offMinutes > 0 ? `incl. ${formatHours(offMinutes)} off` : null;
}

/** "Oct 7 – Oct 9", or "Oct 7" for one day. */
export function leaveSpan(start: string, end: string): string {
  return dateSpan(start, end);
}

/** The browser's local date as YYYY-MM-DD, for defaults in date boxes (the server stays the authority). */
export function todayIso(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}
