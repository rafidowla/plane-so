/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — create-time default + last-used prefill (improvement #19)
import { EIssuePropertyType } from "../types";
import type { TIssueProperty } from "../types";

/**
 * Default + last-used resolution for custom-property values when creating a
 * work item.
 *
 * Priority: the user's last-used value for this project+property (localStorage,
 * keyed per user so shared browsers stay independent) → the property's default
 * (the OPTION flagged `is_default`, else `default_value`) → empty.
 */

const LAST_USED_PREFIX = "plane.custom-properties.last-used";

const lastUsedKey = (userId: string | undefined, projectId: string, propertyId: string): string =>
  [LAST_USED_PREFIX, userId ?? "anonymous", projectId, propertyId].join(":");

/** The configured default for a property: active `is_default` option for OPTION, else `default_value`. */
export const getDefaultPropertyValue = (property: TIssueProperty): string[] => {
  if (property.property_type === EIssuePropertyType.OPTION) {
    const defaultOption = (property.options ?? []).find((option) => option.is_default && option.is_active);
    if (defaultOption) return [defaultOption.id];
  }
  return Array.isArray(property.default_value) ? property.default_value : [];
};

/** The user's last-used values for a property, or null when never set/unreadable. */
export const getLastUsedPropertyValue = (
  userId: string | undefined,
  projectId: string,
  propertyId: string
): string[] | null => {
  try {
    const raw = localStorage.getItem(lastUsedKey(userId, projectId, propertyId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    return null;
  }
};

export const setLastUsedPropertyValue = (
  userId: string | undefined,
  projectId: string,
  propertyId: string,
  values: string[]
): void => {
  try {
    localStorage.setItem(lastUsedKey(userId, projectId, propertyId), JSON.stringify(values));
  } catch {
    // localStorage may be unavailable (private mode, quota) — prefill is best-effort
  }
};

/**
 * What to prefill for a property at create time. Last-used wins when it still
 * points at valid (active) options — a deleted option must never be staged.
 * Falls back to the project default, then to empty (no prefill).
 */
export const getPrefillPropertyValue = (
  userId: string | undefined,
  projectId: string,
  property: TIssueProperty
): string[] => {
  const lastUsed = getLastUsedPropertyValue(userId, projectId, property.id);
  if (lastUsed && lastUsed.length > 0) {
    if (property.property_type !== EIssuePropertyType.OPTION) return lastUsed;
    const activeOptionIds = new Set((property.options ?? []).filter((o) => o.is_active).map((o) => o.id));
    const stillValid = lastUsed.filter((value) => activeOptionIds.has(value));
    if (stillValid.length > 0) return stillValid;
  }
  return getDefaultPropertyValue(property);
};
