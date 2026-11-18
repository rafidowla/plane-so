/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- workspace project categories store. Categories are fetched lazily per workspace;
// project -> category is `project.category_id` on the project store (use `getCategoryById` for the name).

import { set } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
import type { TProjectCategory } from "@plane/types";
import { ProjectCategoryService } from "@/services/project/project-category.service";
import type { TProjectCategoryPayload } from "@/services/project/project-category.service";
import type { RootStore } from "@/store/root.store";

const projectCategoryService = new ProjectCategoryService();

export interface IProjectCategoryStore {
  categoryMap: Record<string, TProjectCategory>;
  /** slug -> category ids (unsorted); read through `getCategoriesForWorkspace` */
  categoryIdsByWorkspace: Record<string, string[]>;
  fetchedWorkspaces: Record<string, boolean>;
  loadingWorkspaces: Record<string, boolean>;
  // reads
  /** categories of a workspace ordered by sort_order, then name (case-insensitive) */
  getCategoriesForWorkspace: (workspaceSlug: string | null | undefined) => TProjectCategory[];
  getCategoryById: (categoryId: string | null | undefined) => TProjectCategory | undefined;
  isFetched: (workspaceSlug: string | null | undefined) => boolean;
  isLoading: (workspaceSlug: string | null | undefined) => boolean;
  // actions
  fetchCategories: (workspaceSlug: string) => Promise<TProjectCategory[]>;
  createCategory: (workspaceSlug: string, data: { name: string; sort_order?: number }) => Promise<TProjectCategory>;
  updateCategory: (
    workspaceSlug: string,
    categoryId: string,
    data: TProjectCategoryPayload
  ) => Promise<TProjectCategory>;
  deleteCategory: (workspaceSlug: string, categoryId: string) => Promise<void>;
}

export const compareCategories = (a: TProjectCategory, b: TProjectCategory) =>
  a.sort_order - b.sort_order || a.name.toLowerCase().localeCompare(b.name.toLowerCase());

export class ProjectCategoryStore implements IProjectCategoryStore {
  categoryMap: Record<string, TProjectCategory> = {};
  categoryIdsByWorkspace: Record<string, string[]> = {};
  fetchedWorkspaces: Record<string, boolean> = {};
  loadingWorkspaces: Record<string, boolean> = {};
  rootStore: RootStore;

  constructor(rootStore: RootStore) {
    makeObservable(this, {
      categoryMap: observable,
      categoryIdsByWorkspace: observable,
      fetchedWorkspaces: observable,
      loadingWorkspaces: observable,
      fetchCategories: action,
      createCategory: action,
      updateCategory: action,
      deleteCategory: action,
    });
    this.rootStore = rootStore;
  }

  getCategoriesForWorkspace = computedFn((workspaceSlug: string | null | undefined): TProjectCategory[] => {
    if (!workspaceSlug) return [];
    const ids = this.categoryIdsByWorkspace[workspaceSlug] ?? [];
    return ids
      .map((id) => this.categoryMap[id])
      .filter((category): category is TProjectCategory => !!category)
      .toSorted(compareCategories);
  });

  getCategoryById = computedFn((categoryId: string | null | undefined): TProjectCategory | undefined =>
    categoryId ? this.categoryMap[categoryId] : undefined
  );

  isFetched = computedFn((workspaceSlug: string | null | undefined): boolean =>
    workspaceSlug ? this.fetchedWorkspaces[workspaceSlug] === true : false
  );

  isLoading = computedFn((workspaceSlug: string | null | undefined): boolean =>
    workspaceSlug ? this.loadingWorkspaces[workspaceSlug] === true : false
  );

  fetchCategories = async (workspaceSlug: string): Promise<TProjectCategory[]> => {
    runInAction(() => set(this.loadingWorkspaces, [workspaceSlug], true));
    try {
      const categories = (await projectCategoryService.list(workspaceSlug)) ?? [];
      runInAction(() => {
        // drop categories that no longer exist server-side
        (this.categoryIdsByWorkspace[workspaceSlug] ?? []).forEach((id) => delete this.categoryMap[id]);
        categories.forEach((category) => set(this.categoryMap, [category.id], category));
        set(
          this.categoryIdsByWorkspace,
          [workspaceSlug],
          categories.map((category) => category.id)
        );
        set(this.fetchedWorkspaces, [workspaceSlug], true);
      });
      return categories;
    } finally {
      runInAction(() => set(this.loadingWorkspaces, [workspaceSlug], false));
    }
  };

  createCategory = async (
    workspaceSlug: string,
    data: { name: string; sort_order?: number }
  ): Promise<TProjectCategory> => {
    const category = await projectCategoryService.create(workspaceSlug, data);
    runInAction(() => {
      set(this.categoryMap, [category.id], category);
      set(
        this.categoryIdsByWorkspace,
        [workspaceSlug],
        [...(this.categoryIdsByWorkspace[workspaceSlug] ?? []), category.id]
      );
    });
    return category;
  };

  /** Optimistic: the change shows immediately and is rolled back if the server rejects it. */
  updateCategory = async (
    workspaceSlug: string,
    categoryId: string,
    data: TProjectCategoryPayload
  ): Promise<TProjectCategory> => {
    const previous = this.categoryMap[categoryId];
    if (previous) runInAction(() => set(this.categoryMap, [categoryId], { ...previous, ...data }));
    try {
      const updated = await projectCategoryService.update(workspaceSlug, categoryId, data);
      runInAction(() =>
        set(this.categoryMap, [categoryId], { ...(this.categoryMap[categoryId] ?? previous), ...updated })
      );
      return updated;
    } catch (error) {
      if (previous) runInAction(() => set(this.categoryMap, [categoryId], previous));
      throw error;
    }
  };

  /** Server-confirmed. The server clears the category on its projects; mirror that locally. */
  deleteCategory = async (workspaceSlug: string, categoryId: string): Promise<void> => {
    await projectCategoryService.remove(workspaceSlug, categoryId);
    runInAction(() => {
      delete this.categoryMap[categoryId];
      set(
        this.categoryIdsByWorkspace,
        [workspaceSlug],
        (this.categoryIdsByWorkspace[workspaceSlug] ?? []).filter((id) => id !== categoryId)
      );
      // category ids are unique across workspaces, so this only touches this workspace's projects
      Object.values(this.rootStore.projectRoot.project.projectMap).forEach((project) => {
        if (project.category_id === categoryId) project.category_id = null;
      });
    });
  };
}
