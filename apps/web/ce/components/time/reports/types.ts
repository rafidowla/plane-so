/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — response shapes of `GET time/report/` (the Reports tab) and the Settings
// endpoints. Kept out of worklog/types.ts so parallel slices don't collide there.

// FORK: time-tracking-v2 holidays/leave (PSR-122)
import type { TDayOff } from "../settings/types";

export type TReportScope = "all" | "projects" | "self";

export type TReportStats = {
  logged_minutes: number;
  expected_minutes: number;
  expected_to_date_minutes: number;
  balance_minutes: number;
  balance_to_date_minutes: number;
  weekend_minutes: number;
  entry_count: number;
  // FORK: holidays/leave (PSR-122) — expected minutes removed by holidays and leave in the range
  off_minutes: number;
  // billing fields exist only while the workspace has billing on
  billable_minutes?: number;
  non_billable_minutes?: number;
  billable_amount?: number;
};

export type TReportTiles = TReportStats & {
  member_count: number;
  running_minutes: number;
};

export type TReportSeriesPoint = {
  date: string;
  logged_minutes: number;
  expected_minutes: number;
  is_working_day: boolean;
  is_future: boolean;
  cumulative_logged_minutes: number;
  cumulative_expected_minutes: number;
};

export type TReportPerson = {
  user_id: string;
  display_name: string;
  is_tracked_now: boolean;
};

export type TReportMemberRow = TReportPerson &
  TReportStats & {
    weekly_capacity_minutes: number | null;
    // FORK: holidays/leave (PSR-122) — working days in the range that are (partly) off, by date
    days_off: TDayOff[];
  };

export type TReportWeekRow = TReportStats & {
  week_start: string;
  from: string;
  to: string;
};

export type TReportProjectRow = {
  project_id: string;
  name: string;
  identifier: string;
  logged_minutes: number;
  entry_count: number;
};

export type TReportTaskRow = {
  name: string;
  issue_id: string | null;
  sequence_id: number | null;
  project_id: string | null;
  project_identifier: string | null;
  project_name: string | null;
  is_deleted: boolean;
  logged_minutes: number;
  entry_count: number;
};

export type TTimeReportV2 = {
  from: string;
  to: string;
  today: string;
  timezone: string;
  scope: TReportScope;
  billing_enabled: boolean;
  work_week: { work_days: number[]; minutes_per_day: number; weekly_minutes: number };
  user: (TReportPerson & { weekly_capacity_minutes: number | null }) | null;
  tiles: TReportTiles;
  series: TReportSeriesPoint[];
  members?: TReportMemberRow[];
  members_truncated?: boolean;
  weeks?: TReportWeekRow[];
  projects: {
    results: TReportProjectRow[];
    other_projects: { label: string; logged_minutes: number };
  };
  tasks: {
    results: TReportTaskRow[];
    limit: number;
    truncated: boolean;
    other_projects_minutes: number;
  };
  people: TReportPerson[];
  people_truncated: boolean;
};

export type TTimeReportParams = {
  from: string;
  to: string;
  /** "team" | "me" | a user id */
  user: string;
  taskLimit?: number;
};

// ---- Settings tab ----

export type TTimeSettings = {
  id: string;
  workspace: string;
  timezone: string;
  work_days: number[]; // ISO weekdays, 1 = Mon .. 7 = Sun
  minutes_per_day: number;
  remind_after_minutes: number;
  auto_stop_after_minutes: number;
  idle_check_min_minutes: number;
  idle_check_max_minutes: number;
  allow_member_manual_entry: boolean;
  weekly_approval_enabled: boolean;
  // billing_enabled / updated_at are left out of the response for non-admins
  billing_enabled?: boolean;
  updated_at?: string;
};

export type TTimeSettingsPatch = Partial<
  Pick<
    TTimeSettings,
    | "work_days"
    | "minutes_per_day"
    | "remind_after_minutes"
    | "auto_stop_after_minutes"
    | "idle_check_min_minutes"
    | "idle_check_max_minutes"
    | "allow_member_manual_entry"
    | "weekly_approval_enabled"
    | "billing_enabled"
  >
>;

export type TTrackingHistoryRow = {
  id: string;
  user: string;
  is_tracked: boolean;
  effective_from: string;
  created_at: string;
};

export type TTrackingMember = {
  user_id: string;
  display_name: string;
  role: number;
  is_tracked: boolean;
  history: TTrackingHistoryRow[];
  weekly_capacity_minutes: number | null;
  // FORK: holidays/leave (PSR-122) — the ASSIGNED holiday list; null = the workspace default
  holiday_calendar_id: string | null;
};

export type TTrackingMembersResponse = {
  date: string;
  default_weekly_minutes: number;
  members: TTrackingMember[];
};

export type TCapacityResponse = {
  user_id: string;
  weekly_capacity_minutes: number | null;
  default_weekly_minutes: number;
};
