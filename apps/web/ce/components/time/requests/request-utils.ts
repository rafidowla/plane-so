/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — pure helpers for time-entry requests: workspace-time-zone conversion
// (no date-fns-tz here, so Intl does it), request wording, and the plain-language error messages.
import { formatLoggedMinutes } from "../../issues/worklog/timer-utils";
import type {
  TTimeRequest,
  TTimeRequestAction,
  TTimeRequestApiError,
  TTimeRequestEvent,
  TTimeRequestRole,
  TTimeRequestStatus,
  TTimeRequestType,
} from "../../issues/worklog/types";

// ---------------------------------------------------------------------------
// Time zones
// ---------------------------------------------------------------------------

const FALLBACK_TZ = "UTC";

/** A usable IANA zone name: the given one when Intl accepts it, otherwise UTC. */
export function safeTimeZone(timeZone: string | null | undefined): string {
  if (!timeZone) return FALLBACK_TZ;
  try {
    // throws RangeError for an unknown zone name
    return new Intl.DateTimeFormat("en-US", { timeZone }).resolvedOptions().timeZone && timeZone;
  } catch {
    return FALLBACK_TZ;
  }
}

type TParts = { year: number; month: number; day: number; hour: number; minute: number; second: number };

function zonedParts(ms: number, timeZone: string): TParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(ms));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hour: get("hour") % 24,
    minute: get("minute"),
    second: get("second"),
  };
}

/** Milliseconds the zone is ahead of UTC at the instant `ms`. */
function zoneOffsetMs(ms: number, timeZone: string): number {
  const p = zonedParts(ms, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** `YYYY-MM-DDTHH:mm` (a datetime-local value) -> the UTC instant, reading it as wall-clock time in `timeZone`. */
export function zonedInputToIso(value: string, timeZone: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!m) return null;
  const tz = safeTimeZone(timeZone);
  const guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  let result = guess - zoneOffsetMs(guess, tz);
  // Near a DST change the offset at the first answer can differ; settle on the offset that holds there.
  const settled = guess - zoneOffsetMs(result, tz);
  if (settled !== result) result = settled;
  const date = new Date(result);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** An instant -> `YYYY-MM-DDTHH:mm` wall-clock time in `timeZone`, for a datetime-local input. */
export function isoToZonedInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const ms = new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const p = zonedParts(ms, safeTimeZone(timeZone));
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** The calendar day (`YYYY-MM-DD`) of an instant in `timeZone`. */
export function isoToZonedDate(iso: string, timeZone: string): string {
  return isoToZonedInput(iso, timeZone).slice(0, 10);
}

/** "Mon 12 Oct, 09:00" in the workspace zone. */
export function formatZonedDateTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: safeTimeZone(timeZone),
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

/** "09:00" in the workspace zone. */
export function formatZonedTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: safeTimeZone(timeZone),
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

/** "09:00 – 10:30 (1h 30m)", or the minutes alone when there is no interval. */
export function formatWindow(
  start: string | null | undefined,
  end: string | null | undefined,
  timeZone: string,
  minutes?: number | null
): string {
  if (!start || !end) return minutes ? formatLoggedMinutes(minutes) : "—";
  const span = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60000));
  return `${formatZonedDateTime(start, timeZone)} – ${formatZonedTime(end, timeZone)} (${formatLoggedMinutes(span)})`;
}

// ---------------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------------

export const REQUEST_TYPE_LABEL: Record<TTimeRequestType, string> = {
  missing_time: "Missing time",
  time_change: "Time change",
  deletion: "Deletion",
  auto_stop_review: "Auto-stopped timer",
};

export const REQUEST_STATUS_LABEL: Record<TTimeRequestStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const REQUEST_ACTION_LABEL: Record<TTimeRequestAction, string> = {
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  applied: "Applied to the timesheet",
  withdrawn: "Withdrawn",
  superseded: "Replaced by a newer request",
};

export const ACTOR_ROLE_LABEL: Record<TTimeRequestRole, string> = {
  member: "member",
  project_admin: "project admin",
  workspace_admin: "workspace admin",
  system: "system",
};

export const issueRef = (request: Pick<TTimeRequest, "issue">): string => {
  const { identifier, sequence_id: seq } = request.issue ?? {};
  return identifier && seq ? `${identifier}-${seq}` : "";
};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export type TRequestErrorInfo = {
  title: string;
  message: string;
  code: TTimeRequestApiError["code"] | null;
  /** one line per clashing entry (overlap only) */
  details: string[];
};

const asPayload = (err: unknown): TTimeRequestApiError =>
  typeof err === "object" && err !== null ? (err as TTimeRequestApiError) : {};

const MESSAGES: Partial<Record<NonNullable<TTimeRequestApiError["code"]>, string>> = {
  reason_required: "Tell your approver why you need this.",
  note_required: "Say why you are rejecting this request.",
  edit_directly: "Workspace admins change time directly, so no request is needed.",
  entry_locked: "This entry is locked, so it can't be changed any more.",
  project_tracking_disabled: "Time tracking is turned off for this project.",
  not_tracked: "Time tracking isn't turned on for you in this workspace. Ask a workspace admin.",
  tracking_disabled: "Time tracking is turned off for this workspace.",
  pending_exists: "This entry already has a request waiting for review. Withdraw it first to send a new one.",
  not_pending: "Someone already reviewed this request. The list has been refreshed.",
  stale: "This entry changed after the request was made, so it can't be applied. Reject it and ask for a new request.",
  own_request: "You can't review your own request.",
  not_approver: "You are not an approver for this request.",
  not_requester: "Only the person who made the request can withdraw it.",
  not_found: "That request or entry no longer exists.",
  request_required: "Changing or deleting your own time needs a request to your approver.",
  not_allowed: "You can only change your own time entries.",
  invalid_filter: "That filter isn't valid.",
};

function overlapDetails(payload: TTimeRequestApiError, timeZone: string): string[] {
  return (payload.overlapping ?? []).map((o) => {
    const name = [o.issue_ref, o.issue_name].filter(Boolean).join(" ");
    return `${name || "A time entry"}, ${formatZonedDateTime(o.started_at, timeZone)} – ${formatZonedTime(o.ended_at, timeZone)}`;
  });
}

/** Plain-language wording for a failed request / review / entry call. `err` is the API payload the service rethrows. */
export function describeRequestError(
  err: unknown,
  timeZone = FALLBACK_TZ,
  fallbackTitle = "Could not save"
): TRequestErrorInfo {
  const payload = asPayload(err);
  const code = payload.code ?? null;
  const base = { title: fallbackTitle, code, details: [] as string[] };

  if (code === "overlap") {
    const details = overlapDetails(payload, timeZone);
    const parts = ["That time overlaps time that is already logged."];
    if (payload.overlaps_timer) parts.push("It also overlaps your running timer.");
    return { ...base, message: parts.join(" "), details };
  }
  if (code === "invalid_interval") {
    // The server's own sentence here is already plain ("The end must be after the start.").
    return { ...base, message: payload.error ?? "Check the start and end times." };
  }
  if (code && MESSAGES[code]) return { ...base, message: MESSAGES[code] as string };
  return { ...base, message: payload.error ?? "Something went wrong. Try again." };
}

/** True when the failed answer means the request changed under the reviewer, so the list must reload. */
export const shouldRefreshAfter = (code: TRequestErrorInfo["code"]): boolean =>
  code === "not_pending" || code === "stale" || code === "not_found";

// ---------------------------------------------------------------------------
// Form validation
// ---------------------------------------------------------------------------

export type TWindowCheck =
  | { ok: true; startIso: string; endIso: string; minutes: number }
  | { ok: false; message: string };

const MAX_ENTRY_MINUTES = 24 * 60;

/** Check a start/end pair typed in workspace time before sending it. The server stays the authority. */
export function checkWindow(startInput: string, endInput: string, timeZone: string, nowMs = Date.now()): TWindowCheck {
  const startIso = zonedInputToIso(startInput, timeZone);
  const endIso = zonedInputToIso(endInput, timeZone);
  if (!startIso || !endIso) return { ok: false, message: "Choose a start and an end time." };
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  if (end <= start) return { ok: false, message: "The end must be after the start." };
  if (end > nowMs) return { ok: false, message: "Time can't be logged in the future." };
  const minutes = Math.round((end - start) / 60000);
  if (minutes > MAX_ENTRY_MINUTES) return { ok: false, message: "One entry can cover at most 24 hours." };
  return { ok: true, startIso, endIso, minutes };
}

/** Is `endIso` strictly inside the session `(start, end)`? Used by "Confirm with end time". */
export function isInsideSession(endIso: string | null, start: string | null, end: string | null): boolean {
  if (!endIso || !start || !end) return false;
  const t = new Date(endIso).getTime();
  return t > new Date(start).getTime() && t < new Date(end).getTime();
}

// ---------------------------------------------------------------------------
// Request wording (shared by the rows, the review dialog and the History view)
// ---------------------------------------------------------------------------

/** The "what changes" line for a request. Exported for tests. */
export function describeChange(request: TTimeRequest, timeZone: string): string {
  const next = formatWindow(request.new_started_at, request.new_ended_at, timeZone, request.new_duration);
  const prev = formatWindow(request.old_started_at, request.old_ended_at, timeZone, request.old_duration);
  switch (request.type) {
    case "missing_time":
      return `Add ${next}`;
    case "time_change":
      return `Change ${prev} to ${next}`;
    case "deletion":
      return `Delete ${prev}`;
    case "auto_stop_review":
      return `Timer was stopped automatically: ${prev}`;
  }
}

export function eventLine(event: TTimeRequestEvent, timeZone: string): string {
  const who = event.actor
    ? `${event.actor.display_name} (${ACTOR_ROLE_LABEL[event.actor_role] ?? event.actor_role})`
    : "System";
  return `${REQUEST_ACTION_LABEL[event.action] ?? event.action} · ${who} · ${formatZonedDateTime(event.created_at, timeZone)}`;
}

export type TReviewMode = "approve" | "reject" | "confirm_end";

const isAutoStop = (r: TTimeRequest | null) => r?.type === "auto_stop_review";

export function reviewTitle(request: TTimeRequest | null, mode: TReviewMode): string {
  if (mode === "reject") return "Reject request";
  if (mode === "confirm_end") return "Confirm with end time";
  return isAutoStop(request) ? "Confirm auto-stopped time" : "Approve request";
}

/** Events matching the Approvals type filter (the events endpoint has no type parameter). */
export const matchesType = (event: TTimeRequestEvent, type: "" | TTimeRequestType): boolean =>
  !type || event.request?.type === type;
