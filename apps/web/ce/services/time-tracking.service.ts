/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";
import type {
  TWorklog,
  TWorklogPayload,
  TWorklogTimer,
  TRunningTimer,
  TTimerStartResponse,
  TTimerStopResponse,
  TMyTasksResponse,
  TLiveResponse,
  TClient,
  TTimesheet,
  TTimeReport,
  TJiraImportConfig,
  TJiraPreview,
  TJiraImportJob,
  // FORK: time-tracking-v2 S3b
  TTimeRequest,
  TTimeRequestList,
  TTimeRequestListParams,
  TTimeRequestEventList,
  TTimeRequestEventParams,
  TTimeRequestHistory,
  TTimeRequestSubmit,
  TTimeEntryPatch,
} from "@/plane-web/components/issues/worklog/types";
// FORK: time-tracking-v2 S5 — Reports tab + Settings tab shapes
import type {
  TCapacityResponse,
  TTimeReportParams,
  TTimeReportV2,
  TTimeSettings,
  TTimeSettingsPatch,
  TTrackingMembersResponse,
} from "@/plane-web/components/time/reports/types";

export class TimeTrackingService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // ---- Clients ----
  async getClients(workspaceSlug: string): Promise<TClient[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/clients/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createClient(workspaceSlug: string, data: Partial<TClient>): Promise<TClient> {
    return this.post(`/api/workspaces/${workspaceSlug}/clients/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateClient(workspaceSlug: string, clientId: string, data: Partial<TClient>): Promise<TClient> {
    return this.patch(`/api/workspaces/${workspaceSlug}/clients/${clientId}/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteClient(workspaceSlug: string, clientId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/clients/${clientId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async setProjectClient(workspaceSlug: string, projectId: string, clientId: string | null): Promise<unknown> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/`, { client: clientId })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Timesheets ----
  async getTimesheets(workspaceSlug: string, params: Record<string, string> = {}): Promise<TTimesheet[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/timesheets/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async submitTimesheet(workspaceSlug: string, periodStart: string, periodEnd: string): Promise<TTimesheet> {
    return this.post(`/api/workspaces/${workspaceSlug}/timesheets/submit/`, {
      period_start: periodStart,
      period_end: periodEnd,
    })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async reviewTimesheet(
    workspaceSlug: string,
    timesheetId: string,
    action: "approve" | "reject",
    note = ""
  ): Promise<TTimesheet> {
    return this.post(`/api/workspaces/${workspaceSlug}/timesheets/${timesheetId}/review/`, { action, note })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Reporting ----
  async getTimeReport(workspaceSlug: string, params: Record<string, string>): Promise<TTimeReport> {
    return this.get(`/api/workspaces/${workspaceSlug}/time-report/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  timeReportCsvUrl(workspaceSlug: string, params: Record<string, string>): string {
    // "export", not "format" — DRF reserves "format" for content-negotiation and
    // 404s the whole request before this endpoint's own CSV branch ever runs.
    const qs = new URLSearchParams({ ...params, export: "csv" }).toString();
    return `${API_BASE_URL}/api/workspaces/${workspaceSlug}/time-report/?${qs}`;
  }

  // ---- Jira import (self-service) ----
  async getJiraStatuses(workspaceSlug: string, projectId: string, config: TJiraImportConfig): Promise<string[]> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/jira-import/statuses/`, config)
      .then((res) => res?.data?.statuses ?? [])
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async previewJiraImport(workspaceSlug: string, projectId: string, config: TJiraImportConfig): Promise<TJiraPreview> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/jira-import/preview/`, config)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async startJiraImport(workspaceSlug: string, projectId: string, config: TJiraImportConfig): Promise<TJiraImportJob> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/jira-import/`, config)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getJiraImportJob(workspaceSlug: string, projectId: string, jobId: string): Promise<TJiraImportJob> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/jira-import/${jobId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getWorklogs(workspaceSlug: string, projectId: string, issueId: string): Promise<TWorklog[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklogs/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createWorklog(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: TWorklogPayload
  ): Promise<TWorklog> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklogs/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateWorklog(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    worklogId: string,
    data: Partial<TWorklogPayload>
  ): Promise<TWorklog> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklogs/${worklogId}/`,
      data
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteWorklog(workspaceSlug: string, projectId: string, issueId: string, worklogId: string): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklogs/${worklogId}/`
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getTimer(workspaceSlug: string, projectId: string, issueId: string): Promise<TWorklogTimer> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklog-timer/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // FORK: PSR-105 — the signed-in user's running timer anywhere in the workspace.
  async getRunningTimer(workspaceSlug: string): Promise<TRunningTimer> {
    // FORK: time-tracking-v2 — same payload as the legacy worklog-timer/ route, plus the idle-check schedule.
    return this.get(`/api/workspaces/${workspaceSlug}/time/timer/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async startTimer(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: { description?: string } = {}
  ): Promise<TWorklogTimer> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklog-timer/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async stopTimer(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: { description?: string } = {}
  ): Promise<TWorklog> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/worklog-timer/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- FORK: time-tracking-v2 — workspace-level timer + live dashboard ----
  /** Start a timer on any work item the caller can track (stops a running one, may move Todo -> started). */
  async startWorkspaceTimer(
    workspaceSlug: string,
    issueId: string,
    data: { description?: string } = {}
  ): Promise<TTimerStartResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/timer/start/`, { issue_id: issueId, ...data })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async stopWorkspaceTimer(workspaceSlug: string, data: { description?: string } = {}): Promise<TTimerStopResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/timer/stop/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** "Yes, still working". 200 = the updated timer; a late answer is an error payload with `code` idle_stopped/auto_stopped. */
  async confirmTimer(workspaceSlug: string): Promise<TRunningTimer> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/timer/confirm/`, {})
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getMyTasks(workspaceSlug: string, q = "", limit?: number): Promise<TMyTasksResponse> {
    const params: Record<string, string | number> = {};
    if (q) params.q = q;
    if (limit) params.limit = limit;
    return this.get(`/api/workspaces/${workspaceSlug}/time/my-tasks/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getLive(workspaceSlug: string, startDate?: string, endDate?: string): Promise<TLiveResponse> {
    const params: Record<string, string> = {};
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    return this.get(`/api/workspaces/${workspaceSlug}/time/live/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- FORK: time-tracking-v2 S3b — time-entry requests, approvals, direct entry edits ----
  async getTimeRequests(workspaceSlug: string, params: TTimeRequestListParams = {}): Promise<TTimeRequestList> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/requests/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async submitTimeRequest(workspaceSlug: string, data: TTimeRequestSubmit): Promise<TTimeRequest> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/requests/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async approveTimeRequest(
    workspaceSlug: string,
    requestId: string,
    data: { note?: string; new_ended_at?: string } = {}
  ): Promise<TTimeRequest> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/requests/${requestId}/approve/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async rejectTimeRequest(workspaceSlug: string, requestId: string, note: string): Promise<TTimeRequest> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/requests/${requestId}/reject/`, { note })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async withdrawTimeRequest(workspaceSlug: string, requestId: string): Promise<TTimeRequest> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/requests/${requestId}/withdraw/`, {})
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getTimeRequestHistory(workspaceSlug: string, requestId: string): Promise<TTimeRequestHistory> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/requests/${requestId}/history/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getTimeRequestEvents(
    workspaceSlug: string,
    params: TTimeRequestEventParams = {}
  ): Promise<TTimeRequestEventList> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/requests/events/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** Workspace admins' direct edit of a time entry (members go through a request instead). */
  async updateTimeEntry(workspaceSlug: string, entryId: string, data: TTimeEntryPatch): Promise<unknown> {
    return this.patch(`/api/workspaces/${workspaceSlug}/time/entries/${entryId}/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- FORK: time-tracking-v2 S5 — Reports tab ----
  private reportQuery(params: TTimeReportParams): string {
    const qs = new URLSearchParams({ from: params.from, to: params.to, user: params.user });
    if (params.taskLimit) qs.set("task_limit", String(params.taskLimit));
    return qs.toString();
  }

  async getTimeReportV2(workspaceSlug: string, params: TTimeReportParams): Promise<TTimeReportV2> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/report/?${this.reportQuery(params)}`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** Entry-level CSV of the same range/person (the browser downloads it; the server sets the filename). */
  timeReportV2CsvUrl(workspaceSlug: string, params: TTimeReportParams): string {
    return `${API_BASE_URL}/api/workspaces/${workspaceSlug}/time/report.csv?${this.reportQuery(params)}`;
  }

  // ---- FORK: time-tracking-v2 S5 — Settings tab ----
  async getTimeSettings(workspaceSlug: string): Promise<TTimeSettings> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/settings/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateTimeSettings(workspaceSlug: string, data: TTimeSettingsPatch): Promise<TTimeSettings> {
    return this.patch(`/api/workspaces/${workspaceSlug}/time/settings/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getTrackingMembers(workspaceSlug: string): Promise<TTrackingMembersResponse> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/members/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** Turn a member's tracking on/off from `effective_from` (YYYY-MM-DD, default today). */
  async setMemberTracking(
    workspaceSlug: string,
    userId: string,
    data: { is_tracked: boolean; effective_from?: string }
  ): Promise<unknown> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/members/${userId}/tracking/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** Weekly capacity override in minutes; `null` = follow the workspace work week. */
  async setMemberCapacity(
    workspaceSlug: string,
    userId: string,
    weeklyMinutes: number | null
  ): Promise<TCapacityResponse> {
    return this.put(`/api/workspaces/${workspaceSlug}/time/members/${userId}/capacity/`, {
      weekly_minutes: weeklyMinutes,
    })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}

export const timeTrackingService = new TimeTrackingService();
