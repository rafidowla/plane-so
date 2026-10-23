/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6 — pure helpers for the work item estimate (parsing what a person types,
// wording of revisions, the over-estimate check). No React, so they are unit tested.
import type { TTimeEstimateApiError, TTimeEstimateRevisionKind } from "../types";
import { formatLoggedMinutes } from "../timer-utils";

/** Backend bound (`1..60000` minutes = 1000 h). */
export const MAX_ESTIMATE_MINUTES = 60_000;

export type TParsedEstimate = { ok: true; minutes: number } | { ok: false; message: string };

const HELP = "Enter hours, for example 2, 1.5, 2h 30m or 90m.";

const toNumber = (text: string): number => Number(text.replace(",", "."));

/**
 * Read an estimate typed in hours. A bare number is hours (`2` = 2 h, `1.5` = 1 h 30 m); `2h 30m`,
 * `2h30`, `90m` and `2:30` work too. Returns whole minutes, or a plain-language message.
 */
export function parseEstimateInput(input: string): TParsedEstimate {
  const text = input.trim().toLowerCase();
  if (!text) return { ok: false, message: "Enter an estimate. " + HELP };

  let minutes: number | null = null;
  let m: RegExpMatchArray | null;
  if ((m = text.match(/^(\d+):(\d{1,2})$/))) {
    if (Number(m[2]) > 59) return { ok: false, message: "Minutes must be below 60. " + HELP };
    minutes = Number(m[1]) * 60 + Number(m[2]);
  } else if ((m = text.match(/^(\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hour|hours)?$/))) {
    minutes = Math.round(toNumber(m[1]) * 60);
  } else if (
    (m = text.match(
      /^(?:(\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hour|hours))?\s*(?:(\d+)\s*(?:m|min|mins|minute|minutes)?)?$/
    )) &&
    (m[1] || m[2])
  ) {
    minutes = Math.round(toNumber(m[1] ?? "0") * 60) + Number(m[2] ?? "0");
  }

  if (minutes === null || Number.isNaN(minutes)) return { ok: false, message: "That isn't a time. " + HELP };
  if (minutes < 1) return { ok: false, message: "The estimate must be at least 1 minute." };
  if (minutes > MAX_ESTIMATE_MINUTES) return { ok: false, message: "The estimate can be at most 1000 hours." };
  return { ok: true, minutes };
}

/** 150 -> "2h 30m" (what the edit box starts with), null -> "". */
export const estimateToInput = (minutes: number | null | undefined): string =>
  minutes ? formatLoggedMinutes(minutes) : "";

/** Logged time passed the current estimate (strictly over: equal is on budget). */
export const isOverEstimate = (loggedMinutes: number, estimateMinutes: number | null | undefined): boolean =>
  Boolean(estimateMinutes) && loggedMinutes > (estimateMinutes as number);

/** Whole percent of the estimate used, uncapped; 0 without an estimate. */
export const estimatePercent = (loggedMinutes: number, estimateMinutes: number | null | undefined): number =>
  estimateMinutes ? Math.round((loggedMinutes / estimateMinutes) * 100) : 0;

export const REVISION_LABEL: Record<TTimeEstimateRevisionKind, string> = {
  original_set: "Original estimate",
  original_replaced: "Replaced before work started",
  re_estimate: "Re-estimate",
  cleared: "Cleared",
};

/** "Oct 16, 2026, 3:04 PM" in the workspace time zone; the raw text when the date can't be read. */
export function formatRevisionTime(iso: string, timeZone: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

const ERROR_MESSAGES: Record<string, string> = {
  estimates_off: "Estimates are turned off for this project.",
  estimate_locked: "The original estimate is locked because work has started. Enter a new value to re-estimate.",
};

/** Plain wording for a failed estimate save. `err` is the API payload the service rethrows. */
export function describeEstimateError(err: unknown): string {
  const payload: TTimeEstimateApiError = typeof err === "object" && err !== null ? (err as TTimeEstimateApiError) : {};
  if (payload.code && ERROR_MESSAGES[payload.code]) return ERROR_MESSAGES[payload.code];
  return payload.error ?? "Could not save the estimate. Try again.";
}
