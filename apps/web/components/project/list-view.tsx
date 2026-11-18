/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- Projects page List view: one scrolling table with a sticky header row and sortable
// Name / Key / Lead columns. Empty, loading and "no results" states are handled by the parent (card-list.tsx).

import { observer } from "mobx-react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { ArrowDownOutline, ArrowUpOutline } from "@makeplane/propel/icons";
import { useTranslation } from "@plane/i18n";
import type { TProjectOrderByOptions } from "@plane/types";
import { cn } from "@plane/utils";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useProjectCategory } from "@/hooks/store/use-project-category";
import { useProjectFilter } from "@/hooks/store/use-project-filter";
// local imports
import { ProjectListRow } from "./list-row";

type TSortableColumn = "name" | "key" | "lead";

type Props = {
  projectIds: string[];
};

export const ProjectListView = observer(function ProjectListView(props: Props) {
  const { projectIds } = props;
  const { t } = useTranslation();
  const { workspaceSlug } = useParams();
  const { getProjectById } = useProject();
  const { fetchCategories, isFetched } = useProjectCategory();
  const { currentWorkspaceDisplayFilters, updateDisplayFilters } = useProjectFilter();
  // the Category column needs the workspace's categories
  const slug = workspaceSlug?.toString();
  useSWR(slug && !isFetched(slug) ? `PROJECT_CATEGORIES_${slug}` : null, () => fetchCategories(slug!), {
    revalidateOnFocus: false,
  });

  const orderBy = currentWorkspaceDisplayFilters?.order_by;

  const handleSort = (column: TSortableColumn) => {
    if (!slug) return;
    const next: TProjectOrderByOptions = orderBy === column ? (`-${column}` as TProjectOrderByOptions) : column;
    updateDisplayFilters(slug, { order_by: next });
  };

  const renderSortableHeader = (column: TSortableColumn, label: string) => {
    const isActive = orderBy === column || orderBy === `-${column}`;
    const isDescending = orderBy === `-${column}`;
    return (
      <th
        scope="col"
        aria-sort={isActive ? (isDescending ? "descending" : "ascending") : "none"}
        className="px-3 py-2 text-left text-11 font-medium text-tertiary"
      >
        <button
          type="button"
          className={cn("flex items-center gap-1 hover:text-primary", { "text-primary": isActive })}
          onClick={() => handleSort(column)}
          aria-label={t("workspace_projects.list_view.sort_by", { column: label })}
        >
          {label}
          {isActive &&
            (isDescending ? (
              <ArrowDownOutline className="size-3" aria-hidden="true" />
            ) : (
              <ArrowUpOutline className="size-3" aria-hidden="true" />
            ))}
        </button>
      </th>
    );
  };

  const plainHeaderClassName = "px-3 py-2 text-left text-11 font-medium text-tertiary";

  return (
    // single scrolling list; narrow screens scroll horizontally inside the table container
    <div className="min-h-0 w-full flex-1 overflow-auto">
      <table className="w-full min-w-[880px] border-separate border-spacing-0 text-13">
        <thead className="sticky top-0 z-[2] bg-surface-1">
          <tr className="[&>th]:border-b [&>th]:border-subtle">
            <th scope="col" className={cn(plainHeaderClassName, "w-10 pl-5")}>
              <span className="sr-only">{t("workspace_projects.list_view.col_favorite")}</span>
            </th>
            {renderSortableHeader("name", t("workspace_projects.list_view.col_name"))}
            {renderSortableHeader("key", t("workspace_projects.list_view.col_key"))}
            {renderSortableHeader("lead", t("workspace_projects.list_view.col_lead"))}
            <th scope="col" className={plainHeaderClassName}>
              {t("workspace_projects.list_view.col_category")}
            </th>
            <th scope="col" className={cn(plainHeaderClassName, "w-24")}>
              {t("workspace_projects.list_view.col_url")}
            </th>
            <th scope="col" className={cn(plainHeaderClassName, "w-24 pr-5")}>
              <span className="sr-only">{t("workspace_projects.list_view.col_actions")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {projectIds.map((projectId) => {
            const project = getProjectById(projectId);
            if (!project) return null;
            return <ProjectListRow key={project.id} project={project} />;
          })}
        </tbody>
      </table>
    </div>
  );
});
