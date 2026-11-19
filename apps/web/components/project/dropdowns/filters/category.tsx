/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- Projects page category filter: a searchable multi-select of the workspace's categories plus "No category".
// Clearing every checked value is the "All categories" default.

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { PROJECT_NO_CATEGORY_FILTER } from "@plane/utils";
// components
import { FilterSubmenu } from "@/components/common/filters/filter-submenu";
// hooks
import { useProjectCategory } from "@/hooks/store/use-project-category";

type Props = {
  appliedFilters: string[] | null;
  handleUpdate: (val: string | string[]) => void;
};

export const FilterCategory = observer(function FilterCategory(props: Props) {
  const { appliedFilters, handleUpdate } = props;
  const { t } = useTranslation();
  const { workspaceSlug } = useParams();
  const slug = workspaceSlug?.toString();
  const { fetchCategories, getCategoriesForWorkspace } = useProjectCategory();
  // FORK: PSR-123 -- revalidates on mount so category edits made elsewhere show up (SWR dedupes concurrent calls)
  useSWR(slug ? `PROJECT_CATEGORIES_${slug}` : null, () => fetchCategories(slug!), {
    revalidateOnFocus: false,
  });

  const options = [
    ...(slug ? getCategoriesForWorkspace(slug) : []).map((category) => ({
      value: category.id,
      label: category.name,
    })),
    { value: PROJECT_NO_CATEGORY_FILTER, label: t("workspace_projects.list_view.no_category") },
  ];

  return (
    <FilterSubmenu
      label={t("workspace_projects.list_view.filter_category")}
      options={options}
      appliedFilters={appliedFilters}
      onToggle={handleUpdate}
      searchable
    />
  );
});
