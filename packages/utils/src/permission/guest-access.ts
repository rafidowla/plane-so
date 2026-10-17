/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: guest-access (PSR-119) — pure helpers behind "project Guests can raise and edit their own work items".
// No React, no store access, so they are unit-testable. The API enforces the same rules (a guest that sends a
// non-empty team-only field gets a 400); this module keeps the UI from offering or sending them.

import { EUserPermissions } from "@plane/constants";

type TRole = EUserPermissions | number | string | null | undefined;

const toRoleNumber = (role: TRole): number | undefined => {
  if (role === null || role === undefined || role === "") return undefined;
  const parsed = typeof role === "string" ? parseInt(role, 10) : role;
  return Number.isNaN(parsed) ? undefined : parsed;
};

/**
 * Work item fields a project Guest may set, on create and on update. Attachments are not part of the
 * work item payload (they go through the attachment endpoints) but are guest-settable too, see
 * {@link canGuestEditWorkItem}.
 */
export const GUEST_EDITABLE_WORK_ITEM_FIELDS = [
  "name",
  "description_html",
  "description_json",
  "description_stripped",
  "priority",
  "label_ids",
] as const;

export type TGuestEditableWorkItemField = (typeof GUEST_EDITABLE_WORK_ITEM_FIELDS)[number];

/**
 * Keys that identify the request rather than carry a work item field. They are kept when a payload
 * is reduced to the guest-editable fields (the stores need `project_id`/`id` to build the request URL;
 * `skip_activity` is the description-migration flag).
 */
const GUEST_PAYLOAD_IDENTITY_KEYS = ["id", "project_id", "skip_activity"] as const;

/** Fields only team members (project Member / Admin) may set; a guest never offers or sends these. */
export const GUEST_RESTRICTED_WORK_ITEM_FIELDS = [
  "state_id",
  "assignee_ids",
  "cycle_id",
  "module_ids",
  "start_date",
  "target_date",
  "estimate_point",
  "parent_id",
  "type_id",
  "is_draft",
] as const;

/** True when the role is the project Guest role. Anything unknown (no membership) is not a guest. */
export const isProjectGuestRole = (role: TRole): boolean => toRoleNumber(role) === EUserPermissions.GUEST;

/** True when the role is a team role (project Member or Admin). */
export const isProjectTeamRole = (role: TRole): boolean => {
  const value = toRoleNumber(role);
  return value === EUserPermissions.MEMBER || value === EUserPermissions.ADMIN;
};

/** Any active project member, guests included, may raise a work item in the project. */
export const canCreateWorkItemInProject = (role: TRole): boolean => {
  const value = toRoleNumber(role);
  return value === EUserPermissions.GUEST || isProjectTeamRole(value);
};

export const isGuestEditableWorkItemField = (field: string): field is TGuestEditableWorkItemField =>
  (GUEST_EDITABLE_WORK_ITEM_FIELDS as readonly string[]).includes(field);

type TCanGuestEditWorkItemArgs = {
  role: TRole;
  /** `created_by` of the work item */
  createdBy: string | null | undefined;
  /** id of the signed-in user */
  currentUserId: string | null | undefined;
};

/**
 * A project Guest may edit (the guest-editable fields of) a work item only when they created it.
 * Returns false for anyone who is not a guest: team roles have their own, broader rules.
 */
export const canGuestEditWorkItem = ({ role, createdBy, currentUserId }: TCanGuestEditWorkItemArgs): boolean =>
  isProjectGuestRole(role) && !!createdBy && !!currentUserId && createdBy === currentUserId;

/**
 * Properties a guest may edit inline (list / kanban / spreadsheet rows, peek view) on a work item they created.
 * Every other row chip (state, assignees, dates, cycle, module, estimate) stays disabled for guests. Keys are the
 * display-property keys of the layouts (`priority`, `labels`).
 */
export const GUEST_INLINE_EDITABLE_PROPERTIES = ["priority", "labels"] as const;

export const isGuestInlineEditableProperty = (property: string): boolean =>
  (GUEST_INLINE_EDITABLE_PROPERTIES as readonly string[]).includes(property);

/**
 * Reduces a create/update payload to what a guest may send: the guest-editable fields plus the request
 * identity keys. Team-only fields (state, assignees, cycle, module, dates, estimate, parent, type, draft)
 * and anything unknown are dropped, including the defaults the create form prefills (e.g. the state).
 */
export const pickGuestWorkItemPayload = <T extends object>(payload: T): Partial<T> => {
  const allowed = new Set<string>([...GUEST_EDITABLE_WORK_ITEM_FIELDS, ...GUEST_PAYLOAD_IDENTITY_KEYS]);
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (allowed.has(key) && value !== undefined) result[key] = value;
  }
  return result as Partial<T>;
};
