/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TIssueProperty } from "../types";

// FORK: custom-properties — improvement #20
// "Task Type" is an OPTION-type custom property configured per project; there
// is no dedicated identifier for it, so it is matched by (display) name.
const TASK_TYPE_NAMES = new Set(["task type", "task_type", "tasktype"]);

const normalize = (value: string | undefined | null): string => (value ?? "").trim().toLowerCase();

/** True when the property is the project's "Task Type" property. */
export const isTaskTypeProperty = (property: TIssueProperty): boolean =>
  TASK_TYPE_NAMES.has(normalize(property.display_name)) || TASK_TYPE_NAMES.has(normalize(property.name));

/**
 * Splits a property list into the Task Type property (promoted to the create
 * modal header, next to the project selector) and everything else (stays in
 * the Options section).
 */
export const splitTaskTypeProperty = (
  properties: TIssueProperty[]
): { taskTypeProperty: TIssueProperty | null; otherProperties: TIssueProperty[] } => {
  const taskTypeProperty = properties.find(isTaskTypeProperty) ?? null;
  return {
    taskTypeProperty,
    otherProperties: taskTypeProperty ? properties.filter((p) => p.id !== taskTypeProperty.id) : properties,
  };
};
