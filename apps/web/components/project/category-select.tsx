/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- optional workspace category picker for the create-project and project-settings forms.

import { observer } from "mobx-react";
import Link from "next/link";
import useSWR from "swr";
// plane imports
import { Select } from "@plane/blocks/select";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// hooks
import { useProjectCategory } from "@/hooks/store/use-project-category";
import { useUserPermissions } from "@/hooks/store/user";

type TCategoryOption = { id: string; name: string };

const NO_CATEGORY_ID = "";

type Props = {
  workspaceSlug: string;
  /** current `category_id` (null/undefined = none) */
  value: string | null | undefined;
  onChange: (categoryId: string | null) => void;
  variant: "pill-md" | "select-lg";
  disabled?: boolean;
  tabIndex?: number;
};

export const ProjectCategorySelect = observer(function ProjectCategorySelect(props: Props) {
  const { workspaceSlug, value, onChange, variant, disabled = false, tabIndex } = props;
  const { t } = useTranslation();
  const { fetchCategories, getCategoriesForWorkspace, getCategoryById, isFetched } = useProjectCategory();
  const { allowPermissions } = useUserPermissions();
  // FORK: PSR-123 -- revalidates on mount so category edits made elsewhere show up (SWR dedupes concurrent calls)
  useSWR(workspaceSlug ? `PROJECT_CATEGORIES_${workspaceSlug}` : null, () => fetchCategories(workspaceSlug), {
    revalidateOnFocus: false,
  });
  // derived values
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const categories = getCategoriesForWorkspace(workspaceSlug);
  const noCategory: TCategoryOption = {
    id: NO_CATEGORY_ID,
    name: t("workspace_settings.settings.project_categories.picker.none"),
  };
  const options: TCategoryOption[] = [noCategory, ...categories];
  const selected = (value ? getCategoryById(value) : undefined) ?? noCategory;

  // nothing to pick from: say so instead of showing an empty dropdown
  if (isFetched(workspaceSlug) && categories.length === 0 && !value)
    return (
      <p className="text-caption-md-regular text-tertiary">
        {t("workspace_settings.settings.project_categories.picker.empty_hint")}{" "}
        {isAdmin && (
          <Link
            href={`/${workspaceSlug}/settings/project-categories`}
            className="text-accent-primary hover:underline"
            target="_blank"
          >
            {t("workspace_settings.settings.project_categories.picker.manage_link")}
          </Link>
        )}
      </p>
    );

  return (
    <Select<TCategoryOption>
      getValues={() => options}
      value={selected}
      onChange={(key) => onChange(key ? String(key) : null)}
      getOptionValue={(option) => option.id}
      getOptionLabel={(option) => option.name}
      placeholder={t("workspace_settings.settings.project_categories.picker.placeholder")}
      showSearch={false}
      pinSelected={false}
      contentSizing={variant === "select-lg" ? "anchor" : undefined}
      disabled={disabled}
    >
      <Select.Trigger<TCategoryOption>
        variant={variant}
        tabIndex={tabIndex}
        disabled={disabled}
        className={variant === "select-lg" ? "font-medium" : undefined}
      >
        {(items) => (
          <span className="min-w-0 grow truncate text-left">
            {items[0]?.id ? items[0].name : t("workspace_settings.settings.project_categories.picker.label")}
          </span>
        )}
      </Select.Trigger>
    </Select>
  );
});
