/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type {
  TAgeTrendLookbackDays,
  TDashboardWidgetConfigMap,
  TDashboardWidgetType,
  TViewListPageSize,
} from "./types";

/** Widget-type picker / card labels. */
export const DASHBOARD_WIDGET_TYPE_LABELS: Record<TDashboardWidgetType, string> = {
  distribution_pie: "Status Distribution",
  project_breakdown_pie: "Project Breakdown",
  age_trend: "Issue Age Trend",
  view_list: "Filtered Issue List",
};

export const AGE_TREND_LOOKBACK_OPTIONS: { value: TAgeTrendLookbackDays; label: string }[] = [
  { value: 30, label: "Last 30 days" },
  { value: 60, label: "Last 60 days" },
  { value: 90, label: "Last 90 days" },
];

export const DASHBOARD_WIDGET_PAGE_SIZE_OPTIONS: { value: TViewListPageSize; label: string }[] = [
  { value: 10, label: "10 per page" },
  { value: 25, label: "25 per page" },
  { value: 50, label: "50 per page" },
];

/**
 * Sensible per-type defaults for a freshly created widget's `config`, mirroring
 * the server-side defaults in apps/api/plane/dashboards/serializers.py /
 * widget_data.py (group_by="state", lookback_days=30, page_size=10) so the
 * create form can pre-fill without guessing.
 */
export const DEFAULT_WIDGET_CONFIG: TDashboardWidgetConfigMap = {
  distribution_pie: { group_by: "state", project_ids: null },
  project_breakdown_pie: { project_ids: null },
  age_trend: { project_ids: null, lookback_days: 30 },
  view_list: { page_size: 10 },
};
