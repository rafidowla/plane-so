/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91)
import { useState } from "react";
import { useParams } from "next/navigation";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import type { TIssue } from "@plane/types";
import { canMoveWorkItem } from "@plane/utils";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

/**
 * Shared by every quick-action menu: whether the "Move to project" entry may be offered for this work item
 * (ADMIN / MEMBER of the item's own project, item not archived) plus the open/closed state of the dialog.
 */
export const useMoveWorkItemAction = (issue: TIssue, readOnly = false) => {
  const { workspaceSlug } = useParams();
  const { allowPermissions } = useUserPermissions();
  const [isMoveWorkItemModalOpen, setMoveWorkItemModal] = useState(false);

  const isMovingAllowed = canMoveWorkItem({
    isProjectEditor: allowPermissions(
      [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
      EUserPermissionsLevel.PROJECT,
      workspaceSlug?.toString(),
      issue.project_id ?? undefined
    ),
    archivedAt: issue.archived_at,
    isReadOnly: readOnly,
  });

  return { isMovingAllowed, isMoveWorkItemModalOpen, setMoveWorkItemModal };
};
