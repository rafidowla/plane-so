/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-126
import { describe, expect, it } from "vitest";
import type { TIssue } from "@plane/types";
import { hasLeftRichFilter } from "./rich-filter-match";

const groups: Record<string, string> = { uat: "started", done: "completed", todo: "unstarted" };
const getGroup = (id: string) => groups[id];
const item = (over: Partial<TIssue>): Partial<TIssue> => ({
  state_id: "uat",
  priority: "medium",
  assignee_ids: ["u1"],
  label_ids: [],
  ...over,
});

describe("hasLeftRichFilter", () => {
  it("drops an item moved out of a State filter (the reported case)", () => {
    expect(hasLeftRichFilter({ state_id__in: "uat" }, item({ state_id: "done" }), { state_id: "done" }, getGroup)).toBe(
      true
    );
  });

  it("keeps an item whose new state is still in the filter", () => {
    expect(
      hasLeftRichFilter({ state_id__in: "uat,done" }, item({ state_id: "done" }), { state_id: "done" }, getGroup)
    ).toBe(false);
  });

  it("handles AND groups and state groups", () => {
    const f = { and: [{ state_group__in: "started" }, { priority__in: "medium,high" }] };
    expect(hasLeftRichFilter(f, item({ state_id: "todo" }), { state_id: "todo" }, getGroup)).toBe(true);
    expect(hasLeftRichFilter(f, item({ priority: "low" }), { priority: "low" }, getGroup)).toBe(true);
    expect(hasLeftRichFilter(f, item({ priority: "high" }), { priority: "high" }, getGroup)).toBe(false);
  });

  it("handles multi-value fields (assignees, labels)", () => {
    expect(
      hasLeftRichFilter({ assignee_id__in: "u1" }, item({ assignee_ids: ["u2"] }), { assignee_ids: ["u2"] }, getGroup)
    ).toBe(true);
    expect(
      hasLeftRichFilter(
        { assignee_id__in: "u1" },
        item({ assignee_ids: ["u1", "u2"] }),
        { assignee_ids: ["u1", "u2"] },
        getGroup
      )
    ).toBe(false);
  });

  it("is conservative: no filter, unrelated edit, or unknown condition keeps the item", () => {
    expect(hasLeftRichFilter({}, item({ state_id: "done" }), { state_id: "done" }, getGroup)).toBe(false);
    expect(hasLeftRichFilter({ state_id__in: "uat" }, item({ state_id: "done" }), { name: "x" }, getGroup)).toBe(false);
    expect(
      hasLeftRichFilter({ target_date__range: "2026-01-01,2026-02-01" }, item({}), { state_id: "uat" }, getGroup)
    ).toBe(false);
    expect(
      hasLeftRichFilter({ state_group__in: "started" }, item({ state_id: "zzz" }), { state_id: "zzz" }, getGroup)
    ).toBe(false);
  });
});
