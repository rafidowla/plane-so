/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";
import type {
  TDashboardWidget,
  TDashboardWidgetChartData,
  TDashboardWidgetCreatePayload,
  TDashboardWidgetUpdatePayload,
  TViewListIssuesResponse,
} from "./types";

/**
 * Client for the fork's plane.dashboards endpoints. Mirrors the sibling
 * ce/custom-properties/issue-properties.service.ts pattern exactly. Routes
 * match apps/api/plane/dashboards/urls.py (all mounted under
 * /api/workspaces/<slug>/fork-dashboard/widgets/).
 */
export class CustomDashboardsService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async list(workspaceSlug: string): Promise<TDashboardWidget[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async create(workspaceSlug: string, payload: TDashboardWidgetCreatePayload): Promise<TDashboardWidget> {
    return this.post(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async update(
    workspaceSlug: string,
    widgetId: string,
    payload: TDashboardWidgetUpdatePayload
  ): Promise<TDashboardWidget> {
    return this.patch(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/${widgetId}/`, payload)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async remove(workspaceSlug: string, widgetId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/${widgetId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async getChartData(workspaceSlug: string, widgetId: string): Promise<TDashboardWidgetChartData> {
    return this.get(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/${widgetId}/data/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  /**
   * `params` uses the `BasePaginator` query-param names (`cursor` / `per_page`),
   * matching the existing `cycle.service.ts#workspaceActiveCycles` /
   * `sticky.service.ts` convention for endpoints built on
   * `apps/api/plane/utils/paginator.py` — NOT `page`/`page_size`, which that
   * paginator does not read.
   */
  async getIssues(
    workspaceSlug: string,
    widgetId: string,
    params?: { cursor?: string; per_page?: number }
  ): Promise<TViewListIssuesResponse> {
    return this.get(`/api/workspaces/${workspaceSlug}/fork-dashboard/widgets/${widgetId}/issues/`, { params })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}

export const customDashboardsService = new CustomDashboardsService();
