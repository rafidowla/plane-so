/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) — wire types for the move preview / move / draft project-mapping endpoints
// (see docs/move-work-item-design.md §5). Fork-owned file; kept here (not in @plane/types) so the pure helpers in
// ./move.ts and the web service share one definition without touching upstream-owned packages.

export type TMoveSubItemsMode = "move" | "detach";

export type TMoveProjectRef = { id: string; name: string; identifier: string };
export type TMoveWorkItemRef = { id: string; name: string; identifier: string };
export type TMoveState = { id: string; name: string; group: string; color: string };
export type TMoveLabel = { id: string; name: string; color: string };
export type TMoveUser = { id: string; display_name: string; avatar_url?: string | null };
export type TMoveProperty = { id: string; display_name: string; property_type: string };

export type TMoveStateMatch = "name" | "group" | "default" | "none";

export type TMoveStateRow = {
  source: TMoveState;
  target: TMoveState | null;
  match: TMoveStateMatch;
  work_item_ids: string[];
};

export type TMoveEstimate = {
  source: { id: string; value: string } | null;
  target: { id: string; value: string } | null;
  action: "kept" | "cleared" | "none";
};

export type TMoveWorklogSummary = {
  count: number;
  total_minutes: number;
  locked_count: number;
  running_timers: number;
  target_time_tracking_enabled: boolean;
  client_changes: boolean;
};

export type TMoveWarning =
  | "TIME_TRACKING_DISABLED_IN_TARGET"
  | "LOCKED_WORKLOGS"
  | "CLIENT_CHANGES"
  | "LABELS_DROPPED"
  | "ASSIGNEES_DROPPED"
  | "PROPERTIES_DROPPED"
  | "CYCLE_CLEARED"
  | "MODULES_CLEARED"
  | "ARCHIVED_SUB_ITEMS_MOVE"
  | "ESTIMATE_CLEARED"
  | "PARENT_DETACHED"
  | "SUB_ITEMS_DETACHED";

export type TMovePlan = {
  source_project: TMoveProjectRef;
  target_project: TMoveProjectRef;
  work_item: TMoveWorkItemRef;
  next_identifier: string;
  sub_items_mode: TMoveSubItemsMode;
  sub_items: TMoveWorkItemRef[];
  /** Archived sub-items that will move too (move-together mode only); they stay archived in the target. */
  archived_sub_items: TMoveWorkItemRef[];
  parent: TMoveWorkItemRef | null;
  states: TMoveStateRow[];
  target_states: TMoveState[];
  labels: { kept: { source: TMoveLabel; target: TMoveLabel }[]; dropped: TMoveLabel[] };
  assignees: { kept: TMoveUser[]; dropped: TMoveUser[] };
  subscribers_dropped: TMoveUser[];
  cycle: { id: string; name: string } | null;
  modules: { id: string; name: string }[];
  estimate: TMoveEstimate;
  issue_type: { action: "kept" | "cleared" | "none" };
  custom_properties: {
    mapped: { source: TMoveProperty; target: TMoveProperty; dropped_options: string[] }[];
    dropped: TMoveProperty[];
  };
  worklogs: TMoveWorklogSummary;
  carried: { comments: number; attachments: number; links: number; relations: number };
  warnings: TMoveWarning[];
};

export type TMovePayload = {
  target_project_id: string;
  sub_items: TMoveSubItemsMode;
  state_map: Record<string, string>;
};

export type TMoveResult = {
  id: string;
  project_id: string;
  sequence_id: number;
  identifier: string;
  moved: { id: string; old_identifier: string; new_identifier: string }[];
  detached_sub_item_ids: string[];
};

export type TDraftProjectMappingPayload = {
  source_project_id: string;
  target_project_id: string;
  state_id?: string | null;
  label_ids?: string[];
  assignee_ids?: string[];
  estimate_point?: string | null;
  type_id?: string | null;
  parent_id?: string | null;
  cycle_id?: string | null;
  module_ids?: string[];
};

export type TDraftProjectMapping = {
  state_id: string | null;
  state_match: TMoveStateMatch;
  label_ids: string[];
  assignee_ids: string[];
  estimate_point: string | null;
  type_id: string | null;
  parent_id: string | null;
  cycle_id: string | null;
  module_ids: string[];
  dropped: {
    labels: TMoveLabel[];
    assignees: TMoveUser[];
    cycle: boolean;
    modules: boolean;
    estimate: boolean;
    parent: boolean;
  };
};
