/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Custom-dashboards frontend types.
 *
 * Mirrors the `plane.dashboards` backend contract exactly — see
 * apps/api/plane/dashboards/{models,serializers,views,widget_data}.py and
 * docs/custom-dashboards-design.md. This is the DATA layer only (Phase 5);
 * no UI types live here.
 */

export type TDashboardWidgetType = "distribution_pie" | "project_breakdown_pie" | "age_trend" | "view_list";

export type TDistributionPieGroupBy = "state" | "state_group" | "priority";

export type TAgeTrendLookbackDays = 30 | 60 | 90;

export type TViewListPageSize = 10 | 25 | 50;

// ---- Per-type config shapes (widget.config, a plain JSON object) ----

export type TDistributionPieWidgetConfig = {
  group_by: TDistributionPieGroupBy;
  project_ids?: string[] | null;
};

export type TProjectBreakdownPieWidgetConfig = {
  project_ids?: string[] | null;
};

export type TAgeTrendWidgetConfig = {
  project_ids?: string[] | null;
  lookback_days?: TAgeTrendLookbackDays;
};

export type TViewListWidgetConfig = {
  page_size?: TViewListPageSize;
};

/** Maps each widget_type to its config shape — the source of truth the other generics key off. */
export type TDashboardWidgetConfigMap = {
  distribution_pie: TDistributionPieWidgetConfig;
  project_breakdown_pie: TProjectBreakdownPieWidgetConfig;
  age_trend: TAgeTrendWidgetConfig;
  view_list: TViewListWidgetConfig;
};

export type TDashboardWidgetConfig = TDashboardWidgetConfigMap[TDashboardWidgetType];

// ---- Widget (matches WorkspaceDashboardWidgetSerializer fields exactly) ----

export type TDashboardWidget<T extends TDashboardWidgetType = TDashboardWidgetType> = {
  id: string;
  workspace: string;
  widget_type: T;
  title: string;
  is_enabled: boolean;
  sort_order: number;
  /** UUID of the backing IssueView. Only meaningful (and required) for view_list widgets. */
  issue_view: string | null;
  config: TDashboardWidgetConfigMap[T];
  created_at: string;
  updated_at: string;
};

// ---- CRUD payloads ----

export type TDashboardWidgetCreatePayload<T extends TDashboardWidgetType = TDashboardWidgetType> = {
  widget_type: T;
  title?: string;
  is_enabled?: boolean;
  sort_order?: number;
  issue_view?: string | null;
  config: TDashboardWidgetConfigMap[T];
};

export type TDashboardWidgetUpdatePayload = Partial<{
  title: string;
  is_enabled: boolean;
  sort_order: number;
  issue_view: string | null;
  config: TDashboardWidgetConfig;
}>;

// ---- Chart data responses (GET .../widgets/<id>/data/) ----

export type TChartDatum = {
  key: string;
  name: string;
  count: number;
};

export type TDistributionPieData = {
  data: TChartDatum[];
  schema: Record<string, unknown>;
  completed_percentage: number | null;
};

export type TProjectBreakdownPieData = {
  data: TChartDatum[];
};

export type TAgeTrendDatum = {
  key: string;
  name: string;
  avg_age_days: number;
  open_count: number;
};

export type TAgeTrendData = {
  data: TAgeTrendDatum[];
};

/** Union of all `.../data/` response shapes (view_list is not served by this endpoint — 400s). */
export type TDashboardWidgetChartData = TDistributionPieData | TProjectBreakdownPieData | TAgeTrendData;

// ---- view_list issues response (GET .../widgets/<id>/issues/) ----

/**
 * One row of `results`, matching `ViewIssueListSerializer.to_representation`
 * (apps/api/plane/app/serializers/view.py) field-for-field.
 */
export type TViewListIssue = {
  id: string;
  name: string;
  state_id: string | null;
  sort_order: number;
  completed_at: string | null;
  estimate_point: string | null;
  priority: string;
  start_date: string | null;
  target_date: string | null;
  sequence_id: number;
  project_id: string;
  parent_id: string | null;
  cycle_id: string | null;
  sub_issues_count: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
  attachment_count: number;
  link_count: number;
  is_draft: boolean;
  archived_at: string | null;
  state__group: string | null;
  assignee_ids: string[];
  label_ids: string[];
  module_ids: string[];
};

/**
 * The standard `BasePaginator.paginate(...)` envelope (apps/api/plane/utils/paginator.py),
 * confirmed by reading its `Response({...})` return value directly — NOT the
 * `{results, total_count, next_cursor}` shape assumed elsewhere; it also includes
 * grouping/stat fields that are always present (null when unused) because
 * `view_list_data` never passes `group_by_field_name`/`extra_stats`.
 */
export type TViewListIssuesEnvelope = {
  grouped_by: string | null;
  sub_grouped_by: string | null;
  total_count: number;
  next_cursor: string;
  prev_cursor: string;
  next_page_results: boolean;
  prev_page_results: boolean;
  count: number;
  total_pages: number;
  total_results: number;
  extra_stats: unknown;
  results: TViewListIssue[];
};

/** Returned instead of the envelope when the widget's saved view was deleted (SET_NULL). */
export type TViewListDeletedResponse = {
  data: [];
  view_deleted: true;
};

export type TViewListIssuesResponse = TViewListIssuesEnvelope | TViewListDeletedResponse;
