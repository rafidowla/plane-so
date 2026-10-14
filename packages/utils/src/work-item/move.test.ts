/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import {
  buildDraftProjectMappingPayload,
  buildMoveStateMap,
  canMoveWorkItem,
  formatMoveConfirmLabel,
  formatMoveDuration,
  getCanonicalWorkItemIdentifier,
  getDraftFormValuesFromMapping,
  getMovedWorkItemHref,
  getMovedWorkItemIds,
  getPostMoveNavigation,
  getSelectedTargetStateId,
  getSequenceFromIdentifier,
  getUnmappedSourceStateIds,
  hasWorklogWarnings,
  shouldShowSubItemsChoice,
  summarizeDraftDrops,
  summarizeMoveDrops,
} from "./move";
import type { TDraftProjectMapping, TMovePlan, TMoveState } from "./move-types";

const state = (id: string, name = id, group = "unstarted"): TMoveState => ({ id, name, group, color: "#000" });

const makePlan = (overrides: Partial<TMovePlan> = {}): TMovePlan => ({
  source_project: { id: "p1", name: "Source", identifier: "PSR" },
  target_project: { id: "p2", name: "Target", identifier: "GFV3" },
  work_item: { id: "w1", name: "Item", identifier: "PSR-12" },
  next_identifier: "GFV3-40",
  sub_items_mode: "move",
  sub_items: [],
  parent: null,
  states: [
    { source: state("s-todo"), target: state("t-todo"), match: "name", work_item_ids: ["w1"] },
    { source: state("s-review"), target: null, match: "none", work_item_ids: ["w1"] },
  ],
  target_states: [state("t-todo"), state("t-backlog"), state("t-done")],
  labels: { kept: [], dropped: [] },
  assignees: { kept: [], dropped: [] },
  subscribers_dropped: [],
  cycle: null,
  modules: [],
  estimate: { source: null, target: null, action: "none" },
  issue_type: { action: "none" },
  custom_properties: { mapped: [], dropped: [] },
  worklogs: {
    count: 0,
    total_minutes: 0,
    locked_count: 0,
    running_timers: 0,
    target_time_tracking_enabled: true,
    client_changes: false,
  },
  carried: { comments: 0, attachments: 0, links: 0, relations: 0 },
  warnings: [],
  ...overrides,
});

describe("buildMoveStateMap", () => {
  it("uses the plan default match when there is no override", () => {
    expect(buildMoveStateMap(makePlan())).toEqual({ "s-todo": "t-todo" });
  });

  it("applies overrides, including for source states the plan could not match", () => {
    expect(buildMoveStateMap(makePlan(), { "s-todo": "t-done", "s-review": "t-backlog" })).toEqual({
      "s-todo": "t-done",
      "s-review": "t-backlog",
    });
  });

  it("ignores overrides that are not states of the target project", () => {
    expect(buildMoveStateMap(makePlan(), { "s-todo": "not-a-state" })).toEqual({ "s-todo": "t-todo" });
  });

  it("ignores overrides for source states that are not in the plan (e.g. after switching to detach)", () => {
    expect(buildMoveStateMap(makePlan(), { "gone-state": "t-done" })).toEqual({ "s-todo": "t-todo" });
  });
});

describe("getUnmappedSourceStateIds / getSelectedTargetStateId", () => {
  it("reports source states with no target and no override", () => {
    expect(getUnmappedSourceStateIds(makePlan())).toEqual(["s-review"]);
    expect(getUnmappedSourceStateIds(makePlan(), { "s-review": "t-backlog" })).toEqual([]);
  });

  it("resolves the selected target state", () => {
    const plan = makePlan();
    expect(getSelectedTargetStateId(plan.states[0], plan.target_states)).toBe("t-todo");
    expect(getSelectedTargetStateId(plan.states[0], plan.target_states, { "s-todo": "t-done" })).toBe("t-done");
    expect(getSelectedTargetStateId(plan.states[1], plan.target_states)).toBeNull();
  });
});

describe("summarizeMoveDrops", () => {
  it("is empty when nothing is lost", () => {
    expect(summarizeMoveDrops(makePlan())).toEqual([]);
  });

  it("lists every kind of loss in a stable order", () => {
    const plan = makePlan({
      labels: { kept: [], dropped: [{ id: "l1", name: "bug", color: "#f00" }] },
      assignees: { kept: [], dropped: [{ id: "u1", display_name: "Ada" }] },
      subscribers_dropped: [{ id: "u2", display_name: "Bob" }],
      cycle: { id: "c1", name: "Sprint 4" },
      modules: [
        { id: "m1", name: "Auth" },
        { id: "m2", name: "Billing" },
      ],
      estimate: { source: { id: "e1", value: "5" }, target: null, action: "cleared" },
      issue_type: { action: "cleared" },
      parent: { id: "w0", name: "Parent", identifier: "PSR-3" },
      custom_properties: {
        mapped: [
          {
            source: { id: "a", display_name: "Stage", property_type: "OPTION" },
            target: { id: "b", display_name: "Stage", property_type: "OPTION" },
            dropped_options: ["Legacy", "Old"],
          },
          {
            source: { id: "c", display_name: "Owner", property_type: "USER" },
            target: { id: "d", display_name: "Owner", property_type: "USER" },
            dropped_options: [],
          },
        ],
        dropped: [{ id: "x", display_name: "Region", property_type: "TEXT" }],
      },
    });
    expect(summarizeMoveDrops(plan)).toEqual([
      { kind: "labels", items: ["bug"] },
      { kind: "assignees", items: ["Ada"] },
      { kind: "subscribers", items: ["Bob"] },
      { kind: "cycle", items: ["Sprint 4"] },
      { kind: "modules", items: ["Auth", "Billing"] },
      { kind: "estimate", items: ["5"] },
      { kind: "issue_type", items: [] },
      { kind: "parent", items: ["PSR-3"] },
      { kind: "custom_properties", items: ["Region"] },
      { kind: "custom_property_options", items: ["Stage: Legacy, Old"] },
    ]);
  });

  it("does not report a kept estimate", () => {
    const plan = makePlan({
      estimate: { source: { id: "e1", value: "5" }, target: { id: "e2", value: "5" }, action: "kept" },
    });
    expect(summarizeMoveDrops(plan)).toEqual([]);
  });
});

const mapping = (dropped: Partial<TDraftProjectMapping["dropped"]>): Pick<TDraftProjectMapping, "dropped"> => ({
  dropped: { labels: [], assignees: [], cycle: false, modules: false, estimate: false, parent: false, ...dropped },
});

describe("summarizeDraftDrops", () => {
  it("is empty when nothing was dropped", () => {
    expect(summarizeDraftDrops(mapping({}))).toEqual([]);
  });

  it("lists dropped labels/assignees by name and flags cleared fields", () => {
    expect(
      summarizeDraftDrops(
        mapping({
          labels: [{ id: "l1", name: "bug", color: "#f00" }],
          assignees: [{ id: "u1", display_name: "Ada" }],
          cycle: true,
          modules: true,
          estimate: true,
          parent: true,
        })
      )
    ).toEqual([
      { kind: "labels", items: ["bug"] },
      { kind: "assignees", items: ["Ada"] },
      { kind: "cycle", items: [] },
      { kind: "modules", items: [] },
      { kind: "estimate", items: [] },
      { kind: "parent", items: [] },
    ]);
  });
});

describe("sub-items choice", () => {
  it("is shown only when the plan has sub-items", () => {
    expect(shouldShowSubItemsChoice(null)).toBe(false);
    expect(shouldShowSubItemsChoice(makePlan())).toBe(false);
    expect(shouldShowSubItemsChoice(makePlan({ sub_items: [{ id: "w2", name: "Sub", identifier: "PSR-13" }] }))).toBe(
      true
    );
  });
});

describe("formatMoveConfirmLabel", () => {
  it("prefers the identifier, falls back to the name, then to the bare prefix", () => {
    expect(formatMoveConfirmLabel({ identifier: "GFV3", name: "Groundfloor v3" })).toBe("Move to GFV3");
    expect(formatMoveConfirmLabel({ identifier: "", name: "Groundfloor v3" })).toBe("Move to Groundfloor v3");
    expect(formatMoveConfirmLabel(null)).toBe("Move to");
    expect(formatMoveConfirmLabel({ identifier: "GFV3", name: "x" }, "Verschieben nach")).toBe("Verschieben nach GFV3");
  });
});

describe("formatMoveDuration", () => {
  it("formats minutes as hours and minutes", () => {
    expect(formatMoveDuration(0)).toBe("0m");
    expect(formatMoveDuration(45)).toBe("45m");
    expect(formatMoveDuration(120)).toBe("2h");
    expect(formatMoveDuration(150)).toBe("2h 30m");
    expect(formatMoveDuration(-5)).toBe("0m");
  });
});

describe("hasWorklogWarnings", () => {
  const base = makePlan().worklogs;
  it("is quiet without entries or conditions", () => {
    expect(hasWorklogWarnings(base)).toBe(false);
    expect(hasWorklogWarnings({ ...base, count: 3 })).toBe(false);
  });
  it("fires for each condition once there are entries", () => {
    expect(hasWorklogWarnings({ ...base, count: 3, target_time_tracking_enabled: false })).toBe(true);
    expect(hasWorklogWarnings({ ...base, count: 3, locked_count: 1 })).toBe(true);
    expect(hasWorklogWarnings({ ...base, count: 3, client_changes: true })).toBe(true);
    expect(hasWorklogWarnings({ ...base, count: 3, running_timers: 1 })).toBe(true);
    expect(hasWorklogWarnings({ ...base, count: 0, locked_count: 1 })).toBe(false);
  });
});

describe("post-move navigation", () => {
  const result = { identifier: "GFV3-40" };
  it("builds the browse href", () => {
    expect(getMovedWorkItemHref("acme", "GFV3-40")).toBe("/acme/browse/GFV3-40/");
  });
  it("navigates from the detail page, closes a peek, stays on a list", () => {
    expect(getPostMoveNavigation("detail", "acme", result)).toEqual({
      type: "navigate",
      href: "/acme/browse/GFV3-40/",
    });
    expect(getPostMoveNavigation("peek", "acme", result)).toEqual({ type: "close-peek" });
    expect(getPostMoveNavigation("list", "acme", result)).toEqual({ type: "none" });
  });
});

describe("getMovedWorkItemIds", () => {
  it("returns the item and its moved sub-items once each", () => {
    expect(
      getMovedWorkItemIds({
        id: "w1",
        moved: [
          { id: "w1", old_identifier: "PSR-12", new_identifier: "GFV3-40" },
          { id: "w2", old_identifier: "PSR-13", new_identifier: "GFV3-41" },
        ],
      })
    ).toEqual(["w1", "w2"]);
  });
});

describe("canMoveWorkItem", () => {
  it("requires an editor role, no archive and no read-only flag", () => {
    expect(canMoveWorkItem({ isProjectEditor: true })).toBe(true);
    expect(canMoveWorkItem({ isProjectEditor: false })).toBe(false);
    expect(canMoveWorkItem({ isProjectEditor: true, archivedAt: "2026-01-01T00:00:00Z" })).toBe(false);
    expect(canMoveWorkItem({ isProjectEditor: true, isReadOnly: true })).toBe(false);
  });
});

describe("identifiers", () => {
  it("parses the sequence from an identifier", () => {
    expect(getSequenceFromIdentifier("GFV3-40")).toBe(40);
    expect(getSequenceFromIdentifier("GFV3")).toBeUndefined();
  });

  it("flags a stale browse URL and returns the canonical identifier", () => {
    expect(
      getCanonicalWorkItemIdentifier({
        urlProjectIdentifier: "PSR",
        urlSequence: "12",
        currentProjectIdentifier: "GFV3",
        currentSequence: 40,
      })
    ).toBe("GFV3-40");
  });

  it("leaves a current URL alone, ignoring identifier case", () => {
    expect(
      getCanonicalWorkItemIdentifier({
        urlProjectIdentifier: "gfv3",
        urlSequence: "40",
        currentProjectIdentifier: "GFV3",
        currentSequence: 40,
      })
    ).toBeUndefined();
  });

  it("does nothing when the current project is unknown", () => {
    expect(
      getCanonicalWorkItemIdentifier({
        urlProjectIdentifier: "PSR",
        urlSequence: "12",
        currentProjectIdentifier: undefined,
        currentSequence: 40,
      })
    ).toBeUndefined();
  });
});

describe("draft project mapping helpers", () => {
  it("builds a payload with safe defaults", () => {
    expect(buildDraftProjectMappingPayload("p1", "p2", { state_id: "s1", label_ids: ["l1"] })).toEqual({
      source_project_id: "p1",
      target_project_id: "p2",
      state_id: "s1",
      label_ids: ["l1"],
      assignee_ids: [],
      estimate_point: null,
      type_id: null,
      parent_id: null,
      cycle_id: null,
      module_ids: [],
    });
  });

  it("extracts form values from a mapping response", () => {
    const mappingResponse: TDraftProjectMapping = {
      state_id: "t1",
      state_match: "name",
      label_ids: ["l9"],
      assignee_ids: [],
      estimate_point: null,
      type_id: null,
      parent_id: null,
      cycle_id: null,
      module_ids: [],
      dropped: { labels: [], assignees: [], cycle: true, modules: false, estimate: false, parent: false },
    };
    expect(getDraftFormValuesFromMapping(mappingResponse)).toEqual({
      state_id: "t1",
      label_ids: ["l9"],
      assignee_ids: [],
      estimate_point: null,
      type_id: null,
      parent_id: null,
      cycle_id: null,
      module_ids: [],
    });
  });
});
