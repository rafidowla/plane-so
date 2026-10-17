/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: guest-access (PSR-119)

import { describe, expect, it } from "vitest";
import { EUserPermissions } from "@plane/constants";
import {
  canCreateWorkItemInProject,
  canGuestEditWorkItem,
  GUEST_EDITABLE_WORK_ITEM_FIELDS,
  GUEST_RESTRICTED_WORK_ITEM_FIELDS,
  isGuestEditableWorkItemField,
  isGuestInlineEditableProperty,
  isProjectGuestRole,
  isProjectTeamRole,
  pickGuestWorkItemPayload,
} from "./guest-access";

describe("role predicates", () => {
  it("recognises the guest role, including a stringified role from the API", () => {
    expect(isProjectGuestRole(EUserPermissions.GUEST)).toBe(true);
    expect(isProjectGuestRole("5")).toBe(true);
    expect(isProjectGuestRole(EUserPermissions.MEMBER)).toBe(false);
    expect(isProjectGuestRole(undefined)).toBe(false);
    expect(isProjectGuestRole(null)).toBe(false);
    expect(isProjectGuestRole("")).toBe(false);
  });

  it("recognises team roles", () => {
    expect(isProjectTeamRole(EUserPermissions.ADMIN)).toBe(true);
    expect(isProjectTeamRole(EUserPermissions.MEMBER)).toBe(true);
    expect(isProjectTeamRole(EUserPermissions.GUEST)).toBe(false);
    expect(isProjectTeamRole(undefined)).toBe(false);
  });

  it("lets every project member raise a work item, but not a non-member", () => {
    expect(canCreateWorkItemInProject(EUserPermissions.GUEST)).toBe(true);
    expect(canCreateWorkItemInProject(EUserPermissions.MEMBER)).toBe(true);
    expect(canCreateWorkItemInProject(EUserPermissions.ADMIN)).toBe(true);
    expect(canCreateWorkItemInProject(undefined)).toBe(false);
    expect(canCreateWorkItemInProject(0)).toBe(false);
  });
});

describe("canGuestEditWorkItem", () => {
  it("allows a guest to edit only the items they created", () => {
    expect(canGuestEditWorkItem({ role: EUserPermissions.GUEST, createdBy: "u1", currentUserId: "u1" })).toBe(true);
    expect(canGuestEditWorkItem({ role: EUserPermissions.GUEST, createdBy: "u2", currentUserId: "u1" })).toBe(false);
  });

  it("is false when the creator or the current user is unknown", () => {
    expect(canGuestEditWorkItem({ role: EUserPermissions.GUEST, createdBy: undefined, currentUserId: "u1" })).toBe(
      false
    );
    expect(canGuestEditWorkItem({ role: EUserPermissions.GUEST, createdBy: "u1", currentUserId: undefined })).toBe(
      false
    );
    expect(canGuestEditWorkItem({ role: EUserPermissions.GUEST, createdBy: "", currentUserId: "" })).toBe(false);
  });

  it("does not apply to team roles (they have their own rules)", () => {
    expect(canGuestEditWorkItem({ role: EUserPermissions.MEMBER, createdBy: "u1", currentUserId: "u1" })).toBe(false);
  });
});

describe("pickGuestWorkItemPayload", () => {
  it("strips every team-only field a create form prefills, even when non-empty", () => {
    const payload = {
      project_id: "p1",
      name: "Broken login",
      description_html: "<p>steps</p>",
      priority: "high",
      label_ids: ["l1"],
      state_id: "s1",
      assignee_ids: ["u2"],
      cycle_id: "c1",
      module_ids: ["m1"],
      start_date: "2026-10-01",
      target_date: "2026-10-09",
      estimate_point: "3",
      parent_id: "w9",
      type_id: "t1",
      is_draft: true,
      sourceIssueId: "w1",
    };
    expect(pickGuestWorkItemPayload(payload)).toEqual({
      project_id: "p1",
      name: "Broken login",
      description_html: "<p>steps</p>",
      priority: "high",
      label_ids: ["l1"],
    });
  });

  it("strips empty team-only defaults too, so the request carries none of them", () => {
    const result = pickGuestWorkItemPayload({
      project_id: "p1",
      name: "x",
      state_id: "",
      assignee_ids: [],
      cycle_id: null,
      module_ids: null,
      type_id: null,
    });
    for (const field of GUEST_RESTRICTED_WORK_ITEM_FIELDS) expect(field in result).toBe(false);
  });

  it("keeps the request identity keys on an update", () => {
    expect(
      pickGuestWorkItemPayload({ id: "w1", project_id: "p1", name: "renamed", type_id: "t1", skip_activity: "true" })
    ).toEqual({ id: "w1", project_id: "p1", name: "renamed", skip_activity: "true" });
  });

  it("drops undefined values and does not mutate the input", () => {
    const payload = { project_id: "p1", name: undefined, priority: "low", state_id: "s1" };
    const result = pickGuestWorkItemPayload(payload);
    expect(result).toEqual({ project_id: "p1", priority: "low" });
    expect(payload.state_id).toBe("s1");
  });

  it("never lets a restricted field into the allow-list", () => {
    for (const field of GUEST_RESTRICTED_WORK_ITEM_FIELDS) expect(isGuestEditableWorkItemField(field)).toBe(false);
    for (const field of GUEST_EDITABLE_WORK_ITEM_FIELDS) expect(isGuestEditableWorkItemField(field)).toBe(true);
  });
});

describe("inline-editable row properties", () => {
  it("is priority and labels only", () => {
    expect(isGuestInlineEditableProperty("priority")).toBe(true);
    expect(isGuestInlineEditableProperty("labels")).toBe(true);
    for (const property of ["state", "assignee", "due_date", "start_date", "cycle", "modules", "estimate", "link"]) {
      expect(isGuestInlineEditableProperty(property)).toBe(false);
    }
  });
});
