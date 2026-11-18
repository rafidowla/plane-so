/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-123
import type { IWorkspaceUserPropertiesResponse } from "@plane/types";

/**
 * Optimistic local merge of a workspace user-properties PATCH.
 * Mirrors the server: top-level keys are replaced, but `display_filters` is shallow-merged so that
 * saving `projects_layout` does not wipe the other display_filters keys held locally.
 */
export const mergeWorkspaceUserProperties = (
  current: IWorkspaceUserPropertiesResponse | undefined,
  data: Partial<IWorkspaceUserPropertiesResponse>
): IWorkspaceUserPropertiesResponse => {
  const merged = { ...current, ...data } as IWorkspaceUserPropertiesResponse;
  if (data.display_filters) merged.display_filters = { ...current?.display_filters, ...data.display_filters };
  return merged;
};
