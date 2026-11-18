/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { sortBy } from "lodash-es";
// plane imports
import type { TProject, TProjectDisplayFilters, TProjectFilters, TProjectOrderByOptions } from "@plane/types";
// local imports
import { getDate } from "./datetime";
import { satisfiesDateFilter } from "./filter";

/**
 * Updates the sort order of the project.
 * @param sortIndex
 * @param destinationIndex
 * @param projectId
 * @returns number | undefined
 */
export const orderJoinedProjects = (
  sourceIndex: number,
  destinationIndex: number,
  currentProjectId: string,
  joinedProjects: TProject[]
): number | undefined => {
  if (!currentProjectId || sourceIndex < 0 || destinationIndex < 0 || joinedProjects.length <= 0) return undefined;

  let updatedSortOrder: number | undefined = undefined;
  const sortOrderDefaultValue = 10000;

  if (destinationIndex === 0) {
    // updating project at the top of the project
    const currentSortOrder = joinedProjects[destinationIndex].sort_order || 0;
    updatedSortOrder = currentSortOrder - sortOrderDefaultValue;
  } else if (destinationIndex === joinedProjects.length) {
    // updating project at the bottom of the project
    const currentSortOrder = joinedProjects[destinationIndex - 1].sort_order || 0;
    updatedSortOrder = currentSortOrder + sortOrderDefaultValue;
  } else {
    // updating project in the middle of the project
    const destinationTopProjectSortOrder = joinedProjects[destinationIndex - 1].sort_order || 0;
    const destinationBottomProjectSortOrder = joinedProjects[destinationIndex].sort_order || 0;
    const updatedValue = (destinationTopProjectSortOrder + destinationBottomProjectSortOrder) / 2;
    updatedSortOrder = updatedValue;
  }

  return updatedSortOrder;
};

export const projectIdentifierSanitizer = (identifier: string): string =>
  identifier.replace(/[^ÇŞĞIİÖÜA-Za-z0-9]/g, "");

// FORK: PSR-123 -- sentinel category filter value that matches projects without a category
export const PROJECT_NO_CATEGORY_FILTER = "none";

// FORK: PSR-123 -- `project_lead` is an id in the store but can be a user object in API payloads
export const getProjectLeadId = (project: Pick<TProject, "project_lead">): string | undefined => {
  const lead = project.project_lead;
  if (!lead) return undefined;
  return typeof lead === "string" ? lead : lead.id;
};

/**
 * @description FORK: PSR-123 -- search matches the project name or key (identifier), case-insensitively
 */
export const projectMatchesSearch = (project: Pick<TProject, "name" | "identifier">, query: string): boolean => {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return project.name.toLowerCase().includes(q) || project.identifier.toLowerCase().includes(q);
};

/**
 * @description filters projects based on the filter
 * @param {TProject} project
 * @param {TProjectFilters} filters
 * @param {TProjectDisplayFilters} displayFilters
 * @returns {boolean}
 */
export const shouldFilterProject = (
  project: TProject,
  displayFilters: TProjectDisplayFilters,
  filters: TProjectFilters
): boolean => {
  let fallsInFilters = true;
  Object.keys(filters).forEach((key) => {
    const filterKey = key as keyof TProjectFilters;
    if (filterKey === "access" && filters.access && filters.access.length > 0)
      fallsInFilters = fallsInFilters && filters.access.includes(`${project.network}`);
    if (filterKey === "lead" && filters.lead && filters.lead.length > 0)
      fallsInFilters = fallsInFilters && filters.lead.includes(`${project.project_lead}`);
    if (filterKey === "members" && filters.members && filters.members.length > 0) {
      const memberIds = project.members;
      fallsInFilters = fallsInFilters && filters.members.some((memberId) => memberIds?.includes(memberId));
    }
    // FORK: PSR-123 -- category filter; "none" selects projects without a category
    if (filterKey === "category" && filters.category && filters.category.length > 0)
      fallsInFilters =
        fallsInFilters &&
        filters.category.some((categoryId) =>
          categoryId === PROJECT_NO_CATEGORY_FILTER ? !project.category_id : project.category_id === categoryId
        );
    if (filterKey === "created_at" && filters.created_at && filters.created_at.length > 0) {
      const createdDate = getDate(project.created_at);
      filters.created_at.forEach((dateFilter) => {
        fallsInFilters = fallsInFilters && !!createdDate && satisfiesDateFilter(createdDate, dateFilter);
      });
    }
  });
  if (displayFilters.my_projects && !project.member_role) fallsInFilters = false;
  if (displayFilters.archived_projects && !project.archived_at) fallsInFilters = false;
  if (project.archived_at) fallsInFilters = displayFilters.archived_projects ? fallsInFilters : false;

  return fallsInFilters;
};

/**
 * @description orders projects based on the orderByKey
 * @param {TProject[]} projects
 * @param {TProjectOrderByOptions | undefined} orderByKey
 * @returns {TProject[]}
 */
export const orderProjects = (
  projects: TProject[],
  orderByKey: TProjectOrderByOptions | undefined,
  // FORK: PSR-123 -- resolves a project's lead display name for the "lead" ordering
  getLeadName?: (project: TProject) => string | undefined
): TProject[] => {
  let orderedProjects: TProject[] = [];
  if (projects.length === 0) return orderedProjects;

  // FORK: PSR-123 -- key and lead orderings
  if (orderByKey === "key" || orderByKey === "-key") {
    const ascending = sortBy(projects, [(p) => p.identifier.toLowerCase(), (p) => p.name.toLowerCase()]);
    // oxlint-disable-next-line unicorn/no-array-reverse -- sortBy returned a fresh array
    return orderByKey === "key" ? ascending : ascending.reverse();
  }
  if (orderByKey === "lead" || orderByKey === "-lead") {
    const direction = orderByKey === "lead" ? 1 : -1;
    const leadName = (p: TProject) => getLeadName?.(p)?.trim().toLowerCase() || undefined;
    // nulls (no lead / unknown member) always sort last, in both directions
    // oxlint-disable-next-line unicorn/no-array-sort -- copy is sorted; toSorted is outside the tsc lib
    return [...projects].sort((a, b) => {
      const nameA = leadName(a);
      const nameB = leadName(b);
      if (nameA === undefined && nameB === undefined) return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
      if (nameA === undefined) return 1;
      if (nameB === undefined) return -1;
      const byLead = nameA.localeCompare(nameB) * direction;
      return byLead !== 0 ? byLead : a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
  }

  if (orderByKey === "sort_order") orderedProjects = sortBy(projects, [(p) => p.sort_order]);
  if (orderByKey === "name") orderedProjects = sortBy(projects, [(p) => p.name.toLowerCase()]);
  // oxlint-disable-next-line unicorn/no-array-reverse -- FORK: PSR-123 -- pre-existing warning; deny-warnings hook needs it clean
  if (orderByKey === "-name") orderedProjects = sortBy(projects, [(p) => p.name.toLowerCase()]).reverse();
  if (orderByKey === "created_at") orderedProjects = sortBy(projects, [(p) => p.created_at]);
  if (orderByKey === "-created_at") orderedProjects = sortBy(projects, [(p) => !p.created_at]);
  if (orderByKey === "members_length") orderedProjects = sortBy(projects, [(p) => p.members?.length]);
  // oxlint-disable-next-line unicorn/no-array-reverse -- FORK: PSR-123 -- pre-existing warning; deny-warnings hook needs it clean
  if (orderByKey === "-members_length") orderedProjects = sortBy(projects, [(p) => p.members?.length]).reverse();

  return orderedProjects;
};
