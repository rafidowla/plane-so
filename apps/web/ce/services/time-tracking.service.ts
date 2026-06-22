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
  TClient,
  TTimesheet,
  TTimeReport,
} from "@/plane-web/components/issues/worklog/types";

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
    const qs = new URLSearchParams({ ...params, format: "csv" }).toString();
    return `${API_BASE_URL}/api/workspaces/${workspaceSlug}/time-report/?${qs}`;
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
}

export const timeTrackingService = new TimeTrackingService();
