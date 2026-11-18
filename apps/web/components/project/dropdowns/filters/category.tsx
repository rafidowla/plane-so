/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- Projects page category filter: "All categories" (default), a multi-select, and "No category".

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { PROJECT_NO_CATEGORY_FILTER } from "@plane/utils";
// components
import { FilterHeader, FilterOption } from "@/components/issues/issue-layouts/filters";
// hooks
import { useProjectCategory } from "@/hooks/store/use-project-category";

type Props = {
  appliedFilters: string[] | null;
  handleUpdate: (val: string | string[]) => void;
  searchQuery: string;
};

export const FilterCategory = observer(function FilterCategory(props: Props) {
  const { appliedFilters, handleUpdate, searchQuery } = props;
  const { t } = useTranslation();
  const { workspaceSlug } = useParams();
  const slug = workspaceSlug?.toString();
  const [previewEnabled, setPreviewEnabled] = useState(true);
  const { fetchCategories, getCategoriesForWorkspace, isFetched } = useProjectCategory();
  useSWR(slug && !isFetched(slug) ? `PROJECT_CATEGORIES_${slug}` : null, () => fetchCategories(slug!), {
    revalidateOnFocus: false,
  });

  const applied = appliedFilters ?? [];
  const query = searchQuery.toLowerCase();
  const categories = (slug ? getCategoriesForWorkspace(slug) : []).filter((c) => c.name.toLowerCase().includes(query));
  const noCategoryLabel = t("workspace_projects.list_view.no_category");
  const allLabel = t("workspace_projects.list_view.all_categories");
  const showAll = allLabel.toLowerCase().includes(query);
  const showNone = noCategoryLabel.toLowerCase().includes(query);

  return (
    <>
      <FilterHeader
        title={`${t("workspace_projects.list_view.filter_category")}${applied.length > 0 ? ` (${applied.length})` : ""}`}
        isPreviewEnabled={previewEnabled}
        handleIsPreviewEnabled={() => setPreviewEnabled(!previewEnabled)}
      />
      {previewEnabled && (
        <div>
          {showAll && (
            <FilterOption
              isChecked={applied.length === 0}
              // clearing = toggling off every applied value (a copy: the handler mutates the stored array)
              onClick={() => {
                if (applied.length > 0) handleUpdate([...applied]);
              }}
              title={allLabel}
            />
          )}
          {categories.map((category) => (
            <FilterOption
              key={category.id}
              isChecked={applied.includes(category.id)}
              onClick={() => handleUpdate(category.id)}
              title={category.name}
            />
          ))}
          {showNone && (
            <FilterOption
              isChecked={applied.includes(PROJECT_NO_CATEGORY_FILTER)}
              onClick={() => handleUpdate(PROJECT_NO_CATEGORY_FILTER)}
              title={noCategoryLabel}
            />
          )}
        </div>
      )}
    </>
  );
});
