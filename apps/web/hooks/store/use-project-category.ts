/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123
import { useContext } from "react";
import { StoreContext } from "@/lib/store-context";
import type { IProjectCategoryStore } from "@/plane-web/store/project-category";

export const useProjectCategory = (): IProjectCategoryStore => {
  const context = useContext(StoreContext);
  if (context === undefined) throw new Error("useProjectCategory must be used within StoreProvider");
  return context.projectCategory;
};
