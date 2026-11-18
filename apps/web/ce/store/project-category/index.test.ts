/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-123
import { observable } from "mobx";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TProjectCategory } from "@plane/types";

const service = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("@/services/project/project-category.service", () => ({
  ProjectCategoryService: class {
    list = service.list;
    create = service.create;
    update = service.update;
    remove = service.remove;
  },
}));

import { ProjectCategoryStore } from "./index";

const cat = (id: string, name: string, sort_order = 0, project_count = 0): TProjectCategory => ({
  id,
  name,
  sort_order,
  project_count,
});

const makeStore = (projects: Record<string, { category_id: string | null }> = {}) => {
  const projectMap = observable(projects);
  const root = { projectRoot: { project: { projectMap } } };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return { store: new ProjectCategoryStore(root as any), projectMap };
};

beforeEach(() => vi.resetAllMocks());

describe("ProjectCategoryStore", () => {
  it("sorts by sort_order, then name case-insensitively", async () => {
    service.list.mockResolvedValue([
      cat("a", "banana", 1),
      cat("b", "Zeta", 0),
      cat("c", "apple", 1),
      cat("d", "Alpha", 0),
    ]);
    const { store } = makeStore();
    await store.fetchCategories("acme");
    expect(store.getCategoriesForWorkspace("acme").map((c) => c.id)).toEqual(["d", "b", "c", "a"]);
    expect(store.isFetched("acme")).toBe(true);
    expect(store.getCategoriesForWorkspace("other")).toEqual([]);
  });

  it("keeps workspaces separate and drops categories deleted server-side on refetch", async () => {
    const { store } = makeStore();
    service.list.mockResolvedValueOnce([cat("a", "A"), cat("b", "B")]);
    await store.fetchCategories("acme");
    service.list.mockResolvedValueOnce([cat("x", "X")]);
    await store.fetchCategories("beta");
    service.list.mockResolvedValueOnce([cat("b", "B")]);
    await store.fetchCategories("acme");
    expect(store.getCategoriesForWorkspace("acme").map((c) => c.id)).toEqual(["b"]);
    expect(store.getCategoryById("a")).toBeUndefined();
    expect(store.getCategoriesForWorkspace("beta").map((c) => c.id)).toEqual(["x"]);
  });

  it("create adds the category in sorted position", async () => {
    service.list.mockResolvedValue([cat("a", "Beta", 0)]);
    const { store } = makeStore();
    await store.fetchCategories("acme");
    service.create.mockResolvedValue(cat("n", "Alpha", 0));
    await store.createCategory("acme", { name: "Alpha" });
    expect(store.getCategoriesForWorkspace("acme").map((c) => c.name)).toEqual(["Alpha", "Beta"]);
  });

  it("create surfaces the server error and adds nothing", async () => {
    service.list.mockResolvedValue([]);
    const { store } = makeStore();
    await store.fetchCategories("acme");
    service.create.mockRejectedValue({ error: "A category with this name already exists in this workspace." });
    await expect(store.createCategory("acme", { name: "dup" })).rejects.toEqual({
      error: "A category with this name already exists in this workspace.",
    });
    expect(store.getCategoriesForWorkspace("acme")).toEqual([]);
  });

  it("update is optimistic and rolls back when the server rejects it", async () => {
    service.list.mockResolvedValue([cat("a", "Old", 0, 3)]);
    const { store } = makeStore();
    await store.fetchCategories("acme");
    let reject!: (e: unknown) => void;
    service.update.mockReturnValue(new Promise((_, r) => (reject = r)));
    const pending = store.updateCategory("acme", "a", { name: "New" });
    expect(store.getCategoryById("a")?.name).toBe("New");
    reject({ name: ["bad"] });
    await expect(pending).rejects.toEqual({ name: ["bad"] });
    expect(store.getCategoryById("a")).toEqual(cat("a", "Old", 0, 3));
  });

  it("update keeps the server response on success", async () => {
    service.list.mockResolvedValue([cat("a", "Old", 0, 3)]);
    const { store } = makeStore();
    await store.fetchCategories("acme");
    service.update.mockResolvedValue(cat("a", "New", 0, 3));
    await store.updateCategory("acme", "a", { name: "New" });
    expect(store.getCategoryById("a")?.name).toBe("New");
  });

  it("delete removes the category and clears category_id on its projects only", async () => {
    service.list.mockResolvedValue([cat("a", "A", 0, 2), cat("b", "B", 0, 1)]);
    const { store, projectMap } = makeStore({
      p1: { category_id: "a" },
      p2: { category_id: "a" },
      p3: { category_id: "b" },
      p4: { category_id: null },
    });
    await store.fetchCategories("acme");
    service.remove.mockResolvedValue(undefined);
    await store.deleteCategory("acme", "a");
    expect(store.getCategoriesForWorkspace("acme").map((c) => c.id)).toEqual(["b"]);
    expect(store.getCategoryById("a")).toBeUndefined();
    expect(projectMap.p1.category_id).toBeNull();
    expect(projectMap.p2.category_id).toBeNull();
    expect(projectMap.p3.category_id).toBe("b");
  });

  it("a failed delete changes nothing", async () => {
    service.list.mockResolvedValue([cat("a", "A", 0, 1)]);
    const { store, projectMap } = makeStore({ p1: { category_id: "a" } });
    await store.fetchCategories("acme");
    service.remove.mockRejectedValue({ error: "forbidden" });
    await expect(store.deleteCategory("acme", "a")).rejects.toBeTruthy();
    expect(store.getCategoryById("a")).toBeDefined();
    expect(projectMap.p1.category_id).toBe("a");
  });
});
