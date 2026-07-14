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

export type TWorklogTimer = {
  id?: string;
  issue?: string;
  user?: string;
  started_at?: string;
  description?: string;
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
};

export type TTimeReport = {
  group_by: "resource" | "project" | "client";
  groups: TTimeReportGroup[];
  totals: {
    total_minutes: number;
    billable_minutes: number;
    billable_amount: number;
    entry_count: number;
  };
};

export type TJiraImportConfig = {
  jira_url?: string;
  jira_email?: string;
  jira_token?: string;
  jira_project?: string;
  jql?: string;
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
