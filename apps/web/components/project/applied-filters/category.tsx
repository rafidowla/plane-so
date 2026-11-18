/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- applied-filter chips for the category filter.

import { observer } from "mobx-react";
import { CloseOutline } from "@makeplane/propel/icons";
import { useTranslation } from "@plane/i18n";
import { PROJECT_NO_CATEGORY_FILTER } from "@plane/utils";
import { useProjectCategory } from "@/hooks/store/use-project-category";

type Props = {
  handleRemove: (val: string) => void;
  values: string[];
  editable: boolean | undefined;
};

export const AppliedCategoryFilters = observer(function AppliedCategoryFilters(props: Props) {
  const { handleRemove, values, editable } = props;
  const { t } = useTranslation();
  const { getCategoryById } = useProjectCategory();
  // the category list may not be loaded yet; skip chips we cannot name rather than showing an id

  return (
    <>
      {values.map((value) => {
        const label =
          value === PROJECT_NO_CATEGORY_FILTER
            ? t("workspace_projects.list_view.no_category")
            : getCategoryById(value)?.name;
        if (!label) return null;
        return (
          <div key={value} className="flex items-center gap-1 rounded-sm bg-layer-1 px-1.5 py-1 text-11">
            <span className="normal-case">{label}</span>
            {editable && (
              <button
                type="button"
                className="grid place-items-center text-tertiary hover:text-secondary"
                onClick={() => handleRemove(value)}
              >
                <CloseOutline height={10} width={10} />
              </button>
            )}
          </div>
        );
      })}
    </>
  );
});
