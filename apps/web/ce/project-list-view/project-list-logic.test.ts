/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-123 -- sorting, category filter and search used by the Projects List view
import { describe, expect, it } from "vitest";
import type { TProject, TProjectDisplayFilters, TProjectFilters } from "@plane/types";
import { orderProjects, projectMatchesSearch, shouldFilterProject } from "@plane/utils";

const project = (id: string, name: string, identifier: string, extra: Partial<TProject> = {}): TProject =>
  ({
    id,
    name,
    identifier,
    network: 2,
    created_at: "2026-01-01T00:00:00Z",
    category_id: null,
    project_lead: null,
    ...extra,
  }) as unknown as TProject;

const leads: Record<string, string> = { u1: "Zoe", u2: "adam", u3: "Mia" };
const leadName = (p: TProject) => (p.project_lead ? leads[p.project_lead as string] : undefined);
const ids = (ps: TProject[]) => ps.map((p) => p.id);

describe("orderProjects key / lead", () => {
  const a = project("a", "Alpha", "ZED");
  const b = project("b", "Bravo", "alp");
  const c = project("c", "Charlie", "MID");

  it("sorts by key case-insensitively, both directions", () => {
    expect(ids(orderProjects([a, b, c], "key"))).toEqual(["b", "c", "a"]);
    expect(ids(orderProjects([a, b, c], "-key"))).toEqual(["a", "c", "b"]);
  });

  it("sorts by lead display name, case-insensitively, ascending and descending", () => {
    const pa = project("a", "Alpha", "A", { project_lead: "u1" });
    const pb = project("b", "Bravo", "B", { project_lead: "u2" });
    const pc = project("c", "Charlie", "C", { project_lead: "u3" });
    expect(ids(orderProjects([pa, pb, pc], "lead", leadName))).toEqual(["b", "c", "a"]);
    expect(ids(orderProjects([pa, pb, pc], "-lead", leadName))).toEqual(["a", "c", "b"]);
  });

  it("puts projects without a lead (or with an unknown lead) last in both directions", () => {
    const pa = project("a", "Alpha", "A", { project_lead: "u1" });
    const none = project("n", "Nolead", "N");
    const unknown = project("x", "Aunknown", "X", { project_lead: "missing" });
    const pb = project("b", "Bravo", "B", { project_lead: "u2" });
    expect(ids(orderProjects([none, pa, unknown, pb], "lead", leadName))).toEqual(["b", "a", "x", "n"]);
    expect(ids(orderProjects([none, pa, unknown, pb], "-lead", leadName))).toEqual(["a", "b", "x", "n"]);
  });

  it("keeps name ordering working", () => {
    expect(ids(orderProjects([c, a, b], "name"))).toEqual(["a", "b", "c"]);
    expect(ids(orderProjects([c, a, b], "-name"))).toEqual(["c", "b", "a"]);
  });
});

describe("category filter", () => {
  const display: TProjectDisplayFilters = {};
  const withCat = project("p1", "One", "ONE", { category_id: "cat-a", project_lead: "u1" });
  const withOther = project("p2", "Two", "TWO", { category_id: "cat-b", project_lead: "u2" });
  const noCat = project("p3", "Three", "THR", { category_id: null, project_lead: "u1" });
  const all = [withCat, withOther, noCat];
  const run = (filters: TProjectFilters, query = "") =>
    ids(all.filter((p) => projectMatchesSearch(p, query) && shouldFilterProject(p, display, filters)));

  it("matches everything with no category filter or an empty one", () => {
    expect(run({})).toEqual(["p1", "p2", "p3"]);
    expect(run({ category: [] })).toEqual(["p1", "p2", "p3"]);
    expect(run({ category: null })).toEqual(["p1", "p2", "p3"]);
  });

  it("multi-selects categories", () => {
    expect(run({ category: ["cat-a"] })).toEqual(["p1"]);
    expect(run({ category: ["cat-a", "cat-b"] })).toEqual(["p1", "p2"]);
  });

  it('"none" matches projects without a category, alone or combined', () => {
    expect(run({ category: ["none"] })).toEqual(["p3"]);
    expect(run({ category: ["none", "cat-b"] })).toEqual(["p2", "p3"]);
  });

  it("combines with the lead filter and search", () => {
    expect(run({ category: ["none"], lead: ["u1"] })).toEqual(["p3"]);
    expect(run({ category: ["cat-b"], lead: ["u1"] })).toEqual([]);
    expect(run({ category: ["cat-a", "none"], lead: ["u1"] }, "thr")).toEqual(["p3"]);
    expect(run({ category: ["cat-a", "none"], lead: ["u1"] }, "two")).toEqual([]);
  });
});

describe("projectMatchesSearch", () => {
  const p = project("p", "Marketing Site", "MKT");
  it("matches by name or key, case-insensitively", () => {
    expect(projectMatchesSearch(p, "")).toBe(true);
    expect(projectMatchesSearch(p, "market")).toBe(true);
    expect(projectMatchesSearch(p, "mkt")).toBe(true);
    expect(projectMatchesSearch(p, "MK")).toBe(true);
    expect(projectMatchesSearch(p, "zzz")).toBe(false);
  });
});
