/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S7 — response of GET /api/workspaces/<slug>/time/estimate-accuracy/.

export type TAccuracyGranularity = "week" | "month";

export type TAccuracyEstimator = { id: string; display_name: string } | null;

/** Aggregate metrics shared by the summary, project, person and trend rows. */
export type TAccuracyStats = {
  n: number;
  original_minutes: number;
  current_minutes: number;
  actual_minutes: number;
  /** Σ actual / Σ original (1 = on target); null when there are no tasks */
  ratio: number | null;
  /** (Σ actual − Σ original) / Σ original, in percent */
  error_pct: number | null;
  ratio_current: number | null;
  median_abs_error_pct: number | null;
  median_abs_error_pct_current: number | null;
  within_count: number;
  /** share of tasks within ±20 % of the original estimate, 0..1 */
  within_share: number | null;
  solo_count: number;
  /** fewer than 5 tasks: shown greyed, read as noise */
  low_sample: boolean;
};

export type TAccuracyTask = {
  issue_id: string;
  sequence_id: number | null;
  name: string;
  is_deleted: boolean;
  project_id: string;
  project_identifier: string | null;
  project_name: string | null;
  estimator: TAccuracyEstimator;
  completed_at: string;
  original_minutes: number;
  current_minutes: number;
  actual_minutes: number;
  /** minutes the original estimator logged on the task */
  estimator_minutes: number;
  reestimated: boolean;
  grandfathered: boolean;
  ratio: number | null;
  error_pct: number | null;
  within: boolean;
  ratio_current: number | null;
  error_pct_current: number | null;
  within_current: boolean | null;
  estimator_share: number | null;
  solo: boolean;
};

export type TAccuracyProject = TAccuracyStats & {
  project_id: string;
  project_identifier: string | null;
  project_name: string | null;
};

export type TAccuracyPerson = TAccuracyStats & { user: TAccuracyEstimator };

export type TAccuracyTrendPoint = TAccuracyStats & {
  /** first day of the week (Monday) or month, YYYY-MM-DD, in the workspace timezone */
  bucket: string;
};

export type TAccuracyResponse = {
  from: string;
  to: string;
  timezone: string;
  scope: "all" | "projects" | "self";
  granularity: TAccuracyGranularity;
  solo: boolean;
  include_grandfathered: boolean;
  thresholds: { within_pct: number; solo_pct: number; min_sample: number };
  excluded: { no_time: number; grandfathered: number; not_solo: number };
  summary: TAccuracyStats;
  tasks: { results: TAccuracyTask[]; limit: number; total: number; truncated: boolean };
  projects: TAccuracyProject[];
  projects_truncated: boolean;
  people: TAccuracyPerson[];
  people_truncated: boolean;
  trend: TAccuracyTrendPoint[];
};

export type TAccuracyParams = {
  from?: string;
  to?: string;
  solo?: boolean;
  granularity?: TAccuracyGranularity;
  include_grandfathered?: boolean;
};
