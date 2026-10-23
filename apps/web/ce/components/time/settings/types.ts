/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — shapes of the holiday-calendar, holiday and leave
// endpoints under `time/`, and the `TDayOff` marker that reports and the timesheet carry.

export type TDayPortion = "full" | "half";

/** A working day that is (partly) off. `portion` is the combined result; `label` is ready to show. */
export type TDayOff = {
  date: string;
  kind: "holiday" | "leave" | "both";
  portion: TDayPortion;
  label: string;
};

export type THolidayCalendar = {
  id: string;
  name: string;
  is_default: boolean;
  member_count: number;
};

export type THolidayCalendarPatch = { name?: string; is_default?: boolean };

export type THoliday = {
  id: string;
  date: string; // YYYY-MM-DD
  name: string;
  portion: TDayPortion;
};

export type THolidayPayload = { date: string; name: string; portion: TDayPortion };

export type TLeave = {
  id: string;
  user_id: string;
  display_name: string;
  start_date: string;
  end_date: string;
  portion: TDayPortion;
  note: string;
  created_by: string;
  created_at: string;
};

export type TLeavePayload = {
  user_id: string;
  start_date: string;
  end_date: string;
  portion: TDayPortion;
  note: string;
};

export type TLeaveParams = { user_id?: string; from?: string; to?: string };

export type THolidayCalendarAssignment = { user_id: string; calendar_id: string | null };

/** Machine-readable codes the holiday/leave endpoints answer with (body: `{ error, code }`). */
export type THolidayLeaveErrorCode =
  | "duplicate_name"
  | "default_required"
  | "default_calendar"
  | "duplicate_date"
  | "invalid_range"
  | "half_day_range"
  | "overlap"
  | "not_a_member"
  | "forbidden";
