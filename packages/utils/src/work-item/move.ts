/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) / draft-project-change (PSR-92) — pure helpers behind the "Move to project"
// dialog and the draft project switcher. Fork-owned; no React, no store access, so they are unit-testable.

import type {
  TDraftProjectMapping,
  TDraftProjectMappingPayload,
  TMovePlan,
  TMoveProjectRef,
  TMoveResult,
  TMoveSubItemsMode,
} from "./move-types";

// ---------------------------------------------------------------------------------------------------------------
// state mapping
// ---------------------------------------------------------------------------------------------------------------

/**
 * Builds the `state_map` request field: for every source state in the plan, the user's override when there is one
 * (and it is a state of the target project), otherwise the plan's own default match. Source states the plan has no
 * target for and the user did not override are left out (the confirm button is disabled for those, see
 * {@link getUnmappedSourceStateIds}).
 */
export const buildMoveStateMap = (
  plan: Pick<TMovePlan, "states" | "target_states">,
  overrides: Record<string, string | undefined> = {}
): Record<string, string> => {
  const targetStateIds = new Set(plan.target_states.map((state) => state.id));
  const stateMap: Record<string, string> = {};
  for (const row of plan.states) {
    const override = overrides[row.source.id];
    const targetId = override && targetStateIds.has(override) ? override : row.target?.id;
    if (targetId) stateMap[row.source.id] = targetId;
  }
  return stateMap;
};

/** Source state ids that end up with no target state (no plan match and no valid override). */
export const getUnmappedSourceStateIds = (
  plan: Pick<TMovePlan, "states" | "target_states">,
  overrides: Record<string, string | undefined> = {}
): string[] => {
  const stateMap = buildMoveStateMap(plan, overrides);
  return plan.states.filter((row) => !stateMap[row.source.id]).map((row) => row.source.id);
};

/** The target state currently selected for a plan row, honouring the user's override. */
export const getSelectedTargetStateId = (
  row: TMovePlan["states"][number],
  targetStates: TMovePlan["target_states"],
  overrides: Record<string, string | undefined> = {}
): string | null => {
  const override = overrides[row.source.id];
  if (override && targetStates.some((state) => state.id === override)) return override;
  return row.target?.id ?? null;
};

// ---------------------------------------------------------------------------------------------------------------
// "what will be dropped" summary
// ---------------------------------------------------------------------------------------------------------------

export type TMoveDropKind =
  | "labels"
  | "assignees"
  | "subscribers"
  | "cycle"
  | "modules"
  | "estimate"
  | "issue_type"
  | "parent"
  | "custom_properties"
  | "custom_property_options";

export type TMoveDropRow = {
  kind: TMoveDropKind;
  /** Names to list next to the row's heading; empty for rows that only need the heading ("estimate cleared"). */
  items: string[];
};

/**
 * Flattens everything the plan says will not survive the move into display rows, in a stable order. Rows with
 * nothing to report are omitted, so an empty result means "nothing is lost".
 */
export const summarizeMoveDrops = (plan: TMovePlan): TMoveDropRow[] => {
  const rows: TMoveDropRow[] = [];
  if (plan.labels.dropped.length > 0) rows.push({ kind: "labels", items: plan.labels.dropped.map((l) => l.name) });
  if (plan.assignees.dropped.length > 0)
    rows.push({ kind: "assignees", items: plan.assignees.dropped.map((u) => u.display_name) });
  if (plan.subscribers_dropped.length > 0)
    rows.push({ kind: "subscribers", items: plan.subscribers_dropped.map((u) => u.display_name) });
  if (plan.cycle) rows.push({ kind: "cycle", items: [plan.cycle.name] });
  if (plan.modules.length > 0) rows.push({ kind: "modules", items: plan.modules.map((m) => m.name) });
  if (plan.estimate.action === "cleared" && plan.estimate.source)
    rows.push({ kind: "estimate", items: [plan.estimate.source.value] });
  if (plan.issue_type.action === "cleared") rows.push({ kind: "issue_type", items: [] });
  if (plan.parent) rows.push({ kind: "parent", items: [plan.parent.identifier] });
  if (plan.custom_properties.dropped.length > 0)
    rows.push({ kind: "custom_properties", items: plan.custom_properties.dropped.map((p) => p.display_name) });
  const droppedOptions = plan.custom_properties.mapped
    .filter((m) => m.dropped_options.length > 0)
    .map((m) => `${m.source.display_name}: ${m.dropped_options.join(", ")}`);
  if (droppedOptions.length > 0) rows.push({ kind: "custom_property_options", items: droppedOptions });
  return rows;
};

/**
 * FORK: archived sub-items that travel with the item. They are allowed to move (and stay archived in the target),
 * but the dialog warns before confirm. Empty unless the plan is in "move together" mode.
 */
export const getArchivedMovingSubItems = (
  plan: Pick<TMovePlan, "archived_sub_items"> | null | undefined
): TMovePlan["archived_sub_items"] => plan?.archived_sub_items ?? [];

/** `PSR-4 Child` labels for the archived sub-items warning, in plan order. */
export const formatArchivedSubItemLabels = (plan: Pick<TMovePlan, "archived_sub_items"> | null | undefined): string[] =>
  getArchivedMovingSubItems(plan).map((item) => `${item.identifier} ${item.name}`.trim());

/** Same idea for the draft project switcher: what the mapping call removed from the form. */
export const summarizeDraftDrops = (mapping: Pick<TDraftProjectMapping, "dropped">): TMoveDropRow[] => {
  const { dropped } = mapping;
  const rows: TMoveDropRow[] = [];
  if (dropped.labels.length > 0) rows.push({ kind: "labels", items: dropped.labels.map((l) => l.name) });
  if (dropped.assignees.length > 0)
    rows.push({ kind: "assignees", items: dropped.assignees.map((u) => u.display_name) });
  if (dropped.cycle) rows.push({ kind: "cycle", items: [] });
  if (dropped.modules) rows.push({ kind: "modules", items: [] });
  if (dropped.estimate) rows.push({ kind: "estimate", items: [] });
  if (dropped.parent) rows.push({ kind: "parent", items: [] });
  return rows;
};

// ---------------------------------------------------------------------------------------------------------------
// dialog behaviour
// ---------------------------------------------------------------------------------------------------------------

/** The "move together / detach" choice only matters when the item actually has sub-items in the same project. */
export const shouldShowSubItemsChoice = (plan: Pick<TMovePlan, "sub_items"> | null | undefined): boolean =>
  !!plan && plan.sub_items.length > 0;

/** Sub-items default to travelling with the item (design §2). */
export const DEFAULT_MOVE_SUB_ITEMS_MODE: TMoveSubItemsMode = "move";

/** Confirm button text, e.g. `Move to GFV3`. The prefix is passed in so the caller can translate it. */
export const formatMoveConfirmLabel = (
  project: Pick<TMoveProjectRef, "identifier" | "name"> | null | undefined,
  prefix = "Move to"
): string => {
  const target = project?.identifier || project?.name;
  return target ? `${prefix} ${target}` : prefix;
};

/** `150` -> `2h 30m`, `45` -> `45m`, `120` -> `2h`, `0` -> `0m`. */
export const formatMoveDuration = (totalMinutes: number): string => {
  const minutes = Math.max(0, Math.round(totalMinutes || 0));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest}m`;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
};

/** True when any work-log related condition deserves a warning in the dialog. */
export const hasWorklogWarnings = (worklogs: TMovePlan["worklogs"]): boolean =>
  worklogs.count > 0 &&
  (!worklogs.target_time_tracking_enabled ||
    worklogs.locked_count > 0 ||
    worklogs.client_changes ||
    worklogs.running_timers > 0);

/** `/{workspace}/browse/{IDENTIFIER}/` — the URL of a work item by its human identifier. */
export const getMovedWorkItemHref = (workspaceSlug: string, identifier: string): string =>
  `/${workspaceSlug}/browse/${identifier}/`;

/** Every work item id that left its project: the item itself plus the sub-items that travelled with it. */
export const getMovedWorkItemIds = (result: Pick<TMoveResult, "id" | "moved">): string[] => {
  const ids = new Set<string>([result.id]);
  for (const moved of result.moved ?? []) ids.add(moved.id);
  return [...ids];
};

export type TMoveNavigationContext = "list" | "detail" | "peek";

export type TPostMoveNavigation = { type: "navigate"; href: string } | { type: "close-peek" } | { type: "none" };

/**
 * Where to go after a successful move: the detail page follows the item to its new URL, a peek just closes (the
 * item is gone from the list behind it), and a list/board row stays put.
 */
export const getPostMoveNavigation = (
  context: TMoveNavigationContext,
  workspaceSlug: string,
  result: Pick<TMoveResult, "identifier">
): TPostMoveNavigation => {
  if (context === "detail") return { type: "navigate", href: getMovedWorkItemHref(workspaceSlug, result.identifier) };
  if (context === "peek") return { type: "close-peek" };
  return { type: "none" };
};

/** Only project admins and members can move (guests cannot, even their own items); archived items must be restored. */
export const canMoveWorkItem = ({
  isProjectEditor,
  archivedAt,
  isReadOnly = false,
}: {
  isProjectEditor: boolean;
  archivedAt?: string | null;
  isReadOnly?: boolean;
}): boolean => isProjectEditor && !archivedAt && !isReadOnly;

/** Parses the numeric sequence out of an identifier like `GFV3-40`. */
export const getSequenceFromIdentifier = (identifier: string): number | undefined => {
  const match = /-(\d+)$/.exec(identifier ?? "");
  return match ? Number(match[1]) : undefined;
};

/**
 * Decides whether a `/browse/{identifier}` URL is stale (an old identifier of a moved item) and returns the
 * canonical identifier to redirect to. `undefined` means: stay where you are.
 */
export const getCanonicalWorkItemIdentifier = ({
  urlProjectIdentifier,
  urlSequence,
  currentProjectIdentifier,
  currentSequence,
}: {
  urlProjectIdentifier: string;
  urlSequence: string | number;
  currentProjectIdentifier: string | undefined;
  currentSequence: string | number | undefined;
}): string | undefined => {
  if (!currentProjectIdentifier || currentSequence === undefined || currentSequence === null) return undefined;
  const sameProject = currentProjectIdentifier.toLowerCase() === urlProjectIdentifier.toLowerCase();
  const sameSequence = String(currentSequence) === String(urlSequence);
  if (sameProject && sameSequence) return undefined;
  return `${currentProjectIdentifier}-${currentSequence}`;
};

// ---------------------------------------------------------------------------------------------------------------
// draft project change (PSR-92)
// ---------------------------------------------------------------------------------------------------------------

type TDraftFormValues = {
  state_id?: string | null;
  label_ids?: string[] | null;
  assignee_ids?: string[] | null;
  estimate_point?: string | null;
  type_id?: string | null;
  parent_id?: string | null;
  cycle_id?: string | null;
  module_ids?: string[] | null;
};

/** Builds the `project-mapping` request body from the project the form was on and its current values. */
export const buildDraftProjectMappingPayload = (
  sourceProjectId: string,
  targetProjectId: string,
  values: TDraftFormValues
): TDraftProjectMappingPayload => ({
  source_project_id: sourceProjectId,
  target_project_id: targetProjectId,
  state_id: values.state_id ?? null,
  label_ids: values.label_ids ?? [],
  assignee_ids: values.assignee_ids ?? [],
  estimate_point: values.estimate_point ?? null,
  type_id: values.type_id ?? null,
  parent_id: values.parent_id ?? null,
  cycle_id: values.cycle_id ?? null,
  module_ids: values.module_ids ?? [],
});

/** The form fields to set from a `project-mapping` response (everything the mapping owns). */
export const getDraftFormValuesFromMapping = (mapping: TDraftProjectMapping) => ({
  state_id: mapping.state_id,
  label_ids: mapping.label_ids ?? [],
  assignee_ids: mapping.assignee_ids ?? [],
  estimate_point: mapping.estimate_point,
  type_id: mapping.type_id,
  parent_id: mapping.parent_id,
  cycle_id: mapping.cycle_id,
  module_ids: mapping.module_ids ?? [],
});
