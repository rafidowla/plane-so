/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — pure helpers for the My timesheet tab: week math on calendar-day strings
// (no Date-in-local-zone surprises), wording, and which action each entry / day offers.
import { formatLoggedMinutes } from "../../issues/worklog/timer-utils";
import { formatZonedTime } from "../requests/request-utils";
import type { TTimesheetDay, TTimesheetEntry, TTimesheetWeek } from "./types";

const DAY_MS = 86_400_000;
const pad = (n: number) => String(n).padStart(2, "0");

const toUtcMs = (day: string): number => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : Number.NaN;
};

const fromUtcMs = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
};

/** `YYYY-MM-DD` shifted by `days` calendar days. Returns the input unchanged when it is not a date. */
export function addDays(day: string, days: number): string {
  const ms = toUtcMs(day);
  return Number.isNaN(ms) ? day : fromUtcMs(ms + days * DAY_MS);
}

/** The Monday of the week that holds `day`. */
export function mondayOf(day: string): string {
  const ms = toUtcMs(day);
  if (Number.isNaN(ms)) return day;
  const weekday = (new Date(ms).getUTCDay() + 6) % 7; // Mon = 0
  return fromUtcMs(ms - weekday * DAY_MS);
}

export const shiftWeek = (weekStart: string, weeks: number): string => addDays(weekStart, weeks * 7);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const parts = (day: string) => {
  const ms = toUtcMs(day);
  if (Number.isNaN(ms)) return null;
  const d = new Date(ms);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth(), date: d.getUTCDate(), weekday: (d.getUTCDay() + 6) % 7 };
};

/** "22 Jun – 28 Jun 2026"; the year shows on both ends when the week crosses New Year. */
export function formatWeekRange(start: string, end: string): string {
  const a = parts(start);
  const b = parts(end);
  if (!a || !b) return "";
  const left = `${a.date} ${MONTHS[a.month]}${a.year === b.year ? "" : ` ${a.year}`}`;
  return `${left} – ${b.date} ${MONTHS[b.month]} ${b.year}`;
}

/** "Mon 22" */
export function formatDayShort(day: string): string {
  const p = parts(day);
  return p ? `${WEEKDAYS[p.weekday]} ${p.date}` : day;
}

/** "Monday 22 June" */
const FULL_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const FULL_MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
export function formatDayLong(day: string): string {
  const p = parts(day);
  return p ? `${FULL_WEEKDAYS[p.weekday]} ${p.date} ${FULL_MONTHS[p.month]}` : day;
}

/** A grid cell / total: "1h 30m", or an empty dash for no time. */
export const formatCell = (minutes: number): string => (minutes > 0 ? formatLoggedMinutes(minutes) : "–");

/** "13:00 – 15:30" in the workspace zone, or "" when the entries have no clock times (manual). */
export function formatSpan(start: string | null, end: string | null, timeZone: string): string {
  if (!start || !end) return "";
  return `${formatZonedTime(start, timeZone)} – ${formatZonedTime(end, timeZone)}`;
}

export type TPace = "none" | "under" | "met" | "over";

/** How a day (or the week so far) compares with what was expected. `none` when nothing was expected. */
export function paceOf(logged: number, expected: number): TPace {
  if (expected <= 0) return logged > 0 ? "over" : "none";
  if (logged >= expected) return logged > expected ? "over" : "met";
  return "under";
}

/** "2h 30m under" / "on target" / "1h over" for the week so far. */
export function describeBalance(logged: number, expectedToDate: number): string {
  const diff = logged - expectedToDate;
  if (expectedToDate <= 0 && logged === 0) return "";
  if (diff === 0) return "on target";
  return `${formatLoggedMinutes(Math.abs(diff))} ${diff < 0 ? "under" : "over"}`;
}

export type TEntryAction =
  | { kind: "edit" } // workspace admin: changes the entry directly
  | { kind: "request_change" } // member: asks the approver
  | { kind: "none"; reason: string };

/** What a viewer can do with one of their own entries. Others' entries are read-only. */
export function entryActionFor(
  week: Pick<TTimesheetWeek, "is_self" | "viewer">,
  entry: Pick<TTimesheetEntry, "is_locked" | "pending_request_id">
): TEntryAction {
  if (!week.is_self) return { kind: "none", reason: "" };
  if (entry.is_locked) return { kind: "none", reason: "Approved and locked" };
  if (entry.pending_request_id) return { kind: "none", reason: "Change pending" };
  return week.viewer.is_workspace_admin ? { kind: "edit" } : { kind: "request_change" };
}

/** Is `day` (a YYYY-MM-DD string) before `today`? */
export const isPastDay = (day: string, today: string): boolean => day < today;

/**
 * The first moment to prefill when someone asks for missing time on a past day: 09:00 on that day
 * (workspace time; the form reads it in that zone).
 */
export const defaultRequestDate = (day: TTimesheetDay): string => day.date;

/** Entries newest first per day already come sorted; this is the badge text for a task's group. */
export const groupBadge = (count: number): string | null => (count > 1 ? String(count) : null);

/** Tab-friendly label for who is being viewed. */
export const viewingLabel = (isSelf: boolean, name: string): string =>
  isSelf ? "My timesheet" : `${name}'s timesheet`;
