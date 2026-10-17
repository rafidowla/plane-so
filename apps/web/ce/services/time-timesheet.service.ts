/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — My timesheet endpoints. Its own file so this slice does not collide with
// the other slices' edits to time-tracking.service.ts.
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";
import type {
  TManualEntryPayload,
  TTimesheetPeople,
  TTimesheetWeek,
} from "@/plane-web/components/time/timesheet/types";

export class TimeTimesheetService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /** One week of one person's time: grid, entries by date, approval state. */
  async getWeek(workspaceSlug: string, params: { weekStart?: string; userId?: string } = {}): Promise<TTimesheetWeek> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/timesheet/`, {
      params: { week_start: params.weekStart || undefined, user_id: params.userId || undefined },
    })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /** People whose timesheet the caller may open (empty for plain members). */
  async getPeople(workspaceSlug: string, q = ""): Promise<TTimesheetPeople> {
    return this.get(`/api/workspaces/${workspaceSlug}/time/timesheet/people/`, { params: { q: q || undefined } })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createEntry(workspaceSlug: string, data: TManualEntryPayload): Promise<unknown> {
    return this.post(`/api/workspaces/${workspaceSlug}/time/entries/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteEntry(workspaceSlug: string, entryId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/time/entries/${entryId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}

export const timeTimesheetService = new TimeTimesheetService();
