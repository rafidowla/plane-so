/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-126 — after an inline edit, tell whether a work item has *definitely* left the list's
// active rich filter, so the list can drop it (and its count) without a reload. Upstream keeps the
// item in place until the next fetch. Deliberately conservative: only conditions on fields an
// inline edit changes are checked, and any property/operator not understood here counts as a match.
import type { TIssue } from "@plane/types";

type TFilterNode = Record<string, unknown>;

/** Issue fields an edit can change that a filter condition can be evaluated against. */
export const RICH_FILTER_CHECKED_FIELDS = ["state_id", "priority", "assignee_ids", "label_ids"] as const;

const toValues = (raw: unknown): string[] => {
  if (Array.isArray(raw)) return raw.map(String);
  if (raw === null || raw === undefined || raw === "") return [];
  return String(raw)
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
};

/** Values the item holds for a filter property, or undefined when this property isn't checked here. */
const itemValues = (
  property: string,
  issue: Partial<TIssue>,
  getStateGroup: (stateId: string) => string | undefined
): string[] | undefined => {
  switch (property) {
    case "state_id":
      return issue.state_id ? [issue.state_id] : [];
    case "state_group": {
      const group = issue.state_id ? getStateGroup(issue.state_id) : undefined;
      return group ? [group] : undefined;
    }
    case "priority":
      return issue.priority ? [issue.priority] : [];
    case "assignee_id":
      return issue.assignee_ids ?? undefined;
    case "label_id":
      return issue.label_ids ?? undefined;
    default:
      return undefined;
  }
};

/** false only when a checked condition definitely fails; unknown conditions pass. */
const conditionMatches = (
  key: string,
  raw: unknown,
  issue: Partial<TIssue>,
  getStateGroup: (stateId: string) => string | undefined
): boolean => {
  const sep = key.lastIndexOf("__");
  if (sep <= 0) return true;
  const property = key.slice(0, sep);
  const operator = key.slice(sep + 2);
  if (operator !== "in" && operator !== "exact") return true;
  const have = itemValues(property, issue, getStateGroup);
  if (have === undefined) return true;
  const wanted = toValues(raw);
  if (wanted.length === 0) return true;
  return have.some((v) => wanted.includes(v));
};

const nodeMatches = (
  node: unknown,
  issue: Partial<TIssue>,
  getStateGroup: (stateId: string) => string | undefined
): boolean => {
  if (!node || typeof node !== "object") return true;
  const obj = node as TFilterNode;
  return Object.entries(obj).every(([key, value]) => {
    if (key === "and") return Array.isArray(value) ? value.every((c) => nodeMatches(c, issue, getStateGroup)) : true;
    if (key === "or") return Array.isArray(value) ? value.some((c) => nodeMatches(c, issue, getStateGroup)) : true;
    if (key === "not") return true; // negation: can't prove a mismatch conservatively, leave it in
    return conditionMatches(key, value, issue, getStateGroup);
  });
};

/**
 * True when the edit touched a checked field and the item no longer satisfies the filter.
 * @param richFilters the list's active rich-filter expression (empty object = no filter)
 * @param changed the fields the edit sent
 */
export function hasLeftRichFilter(
  richFilters: unknown,
  issue: Partial<TIssue>,
  changed: Partial<TIssue>,
  getStateGroup: (stateId: string) => string | undefined
): boolean {
  if (!richFilters || typeof richFilters !== "object" || Object.keys(richFilters).length === 0) return false;
  if (!RICH_FILTER_CHECKED_FIELDS.some((f) => f in changed)) return false;
  return !nodeMatches(richFilters, issue, getStateGroup);
}
