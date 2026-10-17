/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — shapes returned by `GET time/timesheet/` and `GET time/timesheet/people/`.
import type { TTimeRequest } from "../../issues/worklog/types";

export type TTimesheetPerson = { id: string; display_name: string; avatar_url: string | null };

export type TTimesheetDay = {
  date: string; // YYYY-MM-DD
  weekday: number; // ISO 1 (Mon) .. 7 (Sun)
  is_weekend: boolean; // not a work day in this workspace
  is_today: boolean;
  is_future: boolean;
  tracked: boolean;
  expected_minutes: number;
  logged_minutes: number;
  other_projects_minutes: number;
};

export type TTimesheetIssue = {
  id: string;
  ref: string;
  name: string;
  is_deleted: boolean;
};

export type TTimesheetGridIssue = TTimesheetIssue & { minutes: number[]; total: number };

export type TTimesheetGridProject = {
  project: { id: string; name: string; identifier: string };
  minutes: number[];
  total: number;
  issues: TTimesheetGridIssue[];
};

export type TTimesheetEntry = {
  id: string;
  issue_id: string;
  project_id: string;
  logged_date: string;
  started_at: string | null;
  ended_at: string | null;
  duration: number;
  description: string;
  source: string;
  work_type: string | null;
  is_locked: boolean;
  approval_status: string;
  auto_stopped: boolean;
  idle_stopped: boolean;
  request_id: string | null;
  pending_request_id: string | null;
};

export type TTimesheetGroup = {
  issue: TTimesheetIssue;
  project_id: string;
  count: number;
  total_minutes: number;
  span_started_at: string | null;
  span_ended_at: string | null;
  entries: TTimesheetEntry[];
};

export type TTimesheetDate = { date: string; total_minutes: number; groups: TTimesheetGroup[] };

export type TTimesheetApproval = {
  enabled: boolean;
  status: "open" | "submitted" | "approved" | "rejected";
  timesheet_id: string | null;
  review_note: string;
};

export type TTimesheetWeek = {
  week_start: string;
  week_end: string;
  timezone: string;
  today: string;
  user: TTimesheetPerson;
  is_self: boolean;
  /** a project admin looking at someone else: only their projects are itemised */
  scoped: boolean;
  viewer: { is_workspace_admin: boolean; can_add_time: boolean };
  settings: {
    allow_member_manual_entry: boolean;
    weekly_approval_enabled: boolean;
    minutes_per_day: number;
    weekly_minutes: number;
  };
  days: TTimesheetDay[];
  projects: TTimesheetGridProject[];
  totals: { logged: number; expected: number; expected_to_date: number; weekend: number; other_projects: number };
  entries_by_date: TTimesheetDate[];
  entries_truncated: boolean;
  pending_requests: TTimeRequest[];
  approval: TTimesheetApproval;
};

export type TTimesheetPeople = {
  viewer_id: string;
  scope: "all" | "projects" | "self";
  results: TTimesheetPerson[];
};

/** What the manual "Add time" form posts to `time/entries/`. */
export type TManualEntryPayload = {
  issue_id: string;
  logged_date: string;
  duration: number;
  description?: string;
  work_type?: string | null;
};
