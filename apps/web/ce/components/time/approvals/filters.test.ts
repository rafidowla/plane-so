/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import type { TTimeRequest } from "../../issues/worklog/types";
import { EMPTY_FILTERS, filterOptions, hasActiveFilters, withinDates } from "./filters";

const req = (over: Partial<TTimeRequest>): TTimeRequest =>
  ({
    id: "r",
    project_id: "p1",
    issue: { id: "i", identifier: "ABC" },
    requester: { id: "u1", display_name: "Zed" },
    work_date: "2026-06-10",
    ...over,
  }) as TTimeRequest;

describe("approval filters", () => {
  it("knows when nothing is filtered", () => {
    expect(hasActiveFilters(EMPTY_FILTERS)).toBe(false);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, type: "deletion" })).toBe(true);
  });

  it("keeps requests inside an inclusive date range, either end optional", () => {
    const rows = [{ work_date: "2026-06-01" }, { work_date: "2026-06-10" }, { work_date: "2026-06-20" }];
    expect(withinDates(rows, "", "")).toHaveLength(3);
    expect(withinDates(rows, "2026-06-10", "")).toHaveLength(2);
    expect(withinDates(rows, "", "2026-06-10")).toHaveLength(2);
    expect(withinDates(rows, "2026-06-10", "2026-06-10")).toEqual([{ work_date: "2026-06-10" }]);
  });

  it("lists each person and project once, people sorted by name", () => {
    const options = filterOptions([
      req({ id: "1", requester: { id: "u2", display_name: "Zed" } as TTimeRequest["requester"] }),
      req({ id: "2", requester: { id: "u1", display_name: "Amy" } as TTimeRequest["requester"] }),
      req({ id: "3", requester: { id: "u1", display_name: "Amy" } as TTimeRequest["requester"] }),
      req({
        id: "4",
        project_id: "p2",
        issue: { id: "j", identifier: "XYZ" },
        requester: { id: "u1", display_name: "Amy" } as TTimeRequest["requester"],
      }),
    ]);
    expect(options.people.map((p) => p.name)).toEqual(["Amy", "Zed"]);
    expect(options.projects).toEqual([
      { id: "p1", identifier: "ABC" },
      { id: "p2", identifier: "XYZ" },
    ]);
  });
});
