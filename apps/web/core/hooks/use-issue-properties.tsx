/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import type { TIssueServiceType } from "@plane/types";
// FORK: custom-properties
import { useProjectCustomProperties, usePropertyValues } from "@/plane-web/custom-properties";

export const useWorkItemProperties = (
  projectId: string | null | undefined,
  workspaceSlug: string | null | undefined,
  workItemId: string | null | undefined,
  _issueServiceType: TIssueServiceType
) => {
  // FORK: custom-properties — pre-hydrate the peeked item's feature/list/values
  // so the sidebar section renders without opening the full detail page.
  const { enabled } = useProjectCustomProperties(workspaceSlug ?? undefined, projectId ?? undefined);
  const valuesStore = usePropertyValues();
  useEffect(() => {
    if (enabled && projectId && workspaceSlug && workItemId)
      valuesStore.enqueueValueFetch(workspaceSlug, projectId, workItemId);
  }, [enabled, projectId, workspaceSlug, workItemId, valuesStore]);
};
