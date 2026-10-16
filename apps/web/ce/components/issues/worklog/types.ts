/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TWorklogApprovalStatus = "draft" | "submitted" | "approved" | "rejected";

export type TWorklogSource = "timer" | "manual";

export type TUserLite = {
  id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  avatar_url?: string | null;
};

export type TWorklog = {
  id: string;
  issue: string;
  project: string;
  workspace: string;
  logged_by: string;
  logged_by_detail?: TUserLite;
  created_by_detail?: TUserLite;
  duration: number; // minutes
  description: string;
  logged_date: string; // YYYY-MM-DD
  started_at?: string | null;
  ended_at?: string | null;
  source: TWorklogSource;
  work_type?: string | null;
  is_billable: boolean;
  billable_rate?: string | null;
  currency: string;
  approval_status: TWorklogApprovalStatus;
  is_locked: boolean;
  created_by: string;
  created_at: string;
};

export type TWorklogPayload = {
  duration: number;
  logged_date: string;
  description?: string;
  work_type?: string | null;
  is_billable?: boolean;
  logged_by?: string; // on-behalf (admin/PM only)
};

// FORK: PSR-101/105 — a work item as the timer endpoints describe it.
export type TTimerIssueRef = {
  id: string;
  name: string;
  sequence_id: number;
  project_id: string;
  project_identifier: string;
  /** e.g. "PSR-12" */
  ref: string;
};

// FORK: PSR-101 — present on the start response when a running timer on another
// work item was stopped (and logged) to make room for the new one.
export type TStoppedTimer = {
  worklog: TWorklog;
  /** minutes logged for the stopped timer */
  duration: number;
  /** null when the work item can no longer be resolved (deleted/archived) */
  issue: TTimerIssueRef | null;
};

export type TWorklogTimer = {
  id?: string;
  issue?: string;
  user?: string;
  started_at?: string;
  description?: string;
  // FORK: PSR-101 — only on the response of a start that auto-stopped another timer
  stopped?: TStoppedTimer;
};

// FORK: PSR-105 — the user's running timer in a workspace (empty object fields when none runs).
export type TRunningTimer = TWorklogTimer & {
  project?: string;
  issue_detail?: TTimerIssueRef | null;
  /** server clock at response time, used to correct client clock skew */
  server_now?: string;
  // FORK: time-tracking-v2 — limit / "Still working?" schedule (all ISO timestamps, server clock)
  last_confirmed_at?: string | null;
  /** false = the "Still working?" check is off */
  idle_check_enabled?: boolean;
  idle_grace_seconds?: number;
  /**
   * null until the server's random check time has passed (it is never announced in advance), then
   * the open prompt: unanswered by `deadline_at` -> the server stops the timer at `prompted_at`.
   */
  check?: { prompted_at: string; deadline_at: string } | null;
  /** 10 h reminder time / whether the reminder was already sent */
  remind_at?: string;
  auto_stop_at?: string;
  reminded?: boolean;
};

// FORK: time-tracking-v2 — what `time/timer/start/` returns on top of the running timer.
export type TWorkItemState = { id: string; name: string; group: string };
export type TStateChange = { from: TWorkItemState; to: TWorkItemState } | null;
export type TTimerStartResponse = TRunningTimer & {
  stopped?: TStoppedTimer | null;
  state_change?: TStateChange;
};

/** Payload of `time/timer/stop/` (the last piece plus the totals). */
export type TTimerStopResponse = TWorklog & {
  pieces?: TWorklog[];
  total_duration?: number;
  issue_detail?: TTimerIssueRef | null;
};

/** API error codes of the timer endpoints (`code` on the error payload). */
export type TTimerErrorCode =
  | "not_tracked"
  | "tracking_disabled"
  | "project_tracking_disabled"
  | "issue_cancelled"
  | "idle_stopped"
  | "auto_stopped"
  | "already_running";

export type TTimerApiError = {
  error?: string;
  code?: TTimerErrorCode;
  /** present when a timer already runs on the same work item */
  timer?: TRunningTimer;
  stopped?: TTimerStopResponse;
};

// FORK: time-tracking-v2 — `time/my-tasks/` (task picker)
export type TMyTask = {
  id: string;
  name: string;
  sequence_id: number;
  ref: string;
  project_id: string;
  project_identifier: string;
  state_id: string | null;
  state_name: string | null;
  state_group: string | null;
  updated_at: string;
};

export type TMyTasksResponse = { tracked: boolean; results: TMyTask[] };

// FORK: time-tracking-v2 — `time/live/` (dashboard). Built from an allow-list on the server:
// when `timer.private` is true, issue and project are null and nothing else about the work leaks.
export type TLiveUser = {
  id: string;
  display_name: string;
  first_name: string;
  last_name: string;
  avatar_url: string | null;
};

export type TLiveTimer = {
  started_at: string;
  private: boolean;
  label: string | null;
  issue: { id: string; ref: string; name: string } | null;
  project: { id: string; identifier: string; name: string } | null;
};

export type TLiveRangeProject = {
  id: string;
  identifier: string;
  name: string;
  logo_props: Record<string, unknown> | null;
  minutes: number;
};

export type TLiveRange = {
  total_minutes: number;
  projects: TLiveRangeProject[];
  other_projects: { label: string; minutes: number };
};

export type TLiveRow = {
  user: TLiveUser;
  is_me: boolean;
  status: "running" | "stopped";
  timer: TLiveTimer | null;
  last_stopped_at: string | null;
  today_logged_minutes: number;
  today_minutes: number;
  range: TLiveRange | null;
};

export type TLiveResponse = {
  server_now: string;
  date: string;
  timezone: string;
  start_date: string;
  end_date: string;
  scope: "all" | "self";
  rows: TLiveRow[];
};

export type TClient = {
  id: string;
  name: string;
  identifier: string;
  description: string;
  email?: string | null;
  phone?: string | null;
  website?: string | null;
  default_billable_rate?: string | null;
  currency: string;
  is_active: boolean;
  project_count?: number;
  project_ids?: string[];
};

export type TTimesheetStatus = "open" | "submitted" | "approved" | "rejected";

export type TTimesheet = {
  id: string;
  user: string;
  user_detail?: TUserLite;
  period_start: string;
  period_end: string;
  status: TTimesheetStatus;
  submitted_at?: string | null;
  reviewed_by_detail?: TUserLite | null;
  reviewed_at?: string | null;
  review_note?: string;
};

export type TTimeReportGroup = {
  key: string | null;
  name: string;
  total_minutes: number;
  billable_minutes: number;
  non_billable_minutes: number;
  billable_amount: number;
  entry_count: number;
  expected_minutes?: number | null;
  utilization_pct?: number | null;
  issue_id?: string | null;
  sequence_id?: number | null;
  project_id?: string | null;
  project_identifier?: string | null;
  project_name?: string | null;
};

export type TTimeReport = {
  group_by: "resource" | "project" | "client" | "issue";
  groups: TTimeReportGroup[];
  totals: {
    total_minutes: number;
    billable_minutes: number;
    billable_amount: number;
    entry_count: number;
  };
  truncated?: boolean;
  limit?: number | null;
};

export type TJiraImportConfig = {
  jira_url?: string;
  jira_email?: string;
  jira_token?: string;
  jira_project?: string;
  jql?: string;
  statuses?: string[];
  with_worklogs?: boolean;
  with_attachments?: boolean;
  sample?: boolean;
};

export type TJiraPreviewRow = {
  key: string;
  summary: string;
  state: string | null;
  priority: string;
  assignee: string | null;
  labels: number;
  comments: number;
  worklogs: number;
  attachments?: number;
};

export type TJiraPreview = {
  dry_run: boolean;
  fetched: number;
  created: number;
  skipped: number;
  comments: number;
  worklogs: number;
  attachments?: number;
  attachments_created?: number;
  attachments_skipped_size?: number;
  attachments_failed?: number;
  unmapped_states: string[];
  unmapped_users: string[];
  preview: TJiraPreviewRow[];
};

export type TJiraImportJob = {
  id: string;
  status: "queued" | "processing" | "completed" | "failed";
  with_worklogs: boolean;
  with_attachments?: boolean;
  config: Record<string, unknown>;
  total: number;
  processed: number;
  result: Partial<TJiraPreview> | Record<string, never>;
  error: string;
  created_at: string;
  initiated_by_detail?: TUserLite;
};
