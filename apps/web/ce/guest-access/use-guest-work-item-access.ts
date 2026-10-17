/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: guest-access (PSR-119)
import { canCreateWorkItemInProject, canGuestEditWorkItem, isProjectGuestRole } from "@plane/utils";
import { useParams } from "next/navigation";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useUser, useUserPermissions } from "@/hooks/store/user";

/**
 * What the signed-in user is, in a project, as far as raising / editing work items goes.
 * - `isGuest`: the user's role in the project is Guest (workspace admins resolve to Admin, so never guests)
 * - `canCreateWorkItem`: any project member, guests included, may raise a work item there
 * Pass `createdBy` (the work item's `created_by`) to also get `canGuestEdit`, true only for a guest on their own item.
 * Pass `archivedAt` too (the work item's `archived_at`) to get `isGuestEditable`: `canGuestEdit` on a non-archived item.
 * That is the one rule behind the guest-editable priority / labels chips in the peek view and in list, kanban and
 * spreadsheet rows.
 */
export const useGuestWorkItemAccess = (
  workspaceSlug: string | undefined,
  projectId: string | undefined | null,
  createdBy?: string | null,
  archivedAt?: string | null
) => {
  const { data: currentUser } = useUser();
  const { getProjectRoleByWorkspaceSlugAndProjectId } = useUserPermissions();
  const role =
    workspaceSlug && projectId ? getProjectRoleByWorkspaceSlugAndProjectId(workspaceSlug, projectId) : undefined;
  const canGuestEdit = canGuestEditWorkItem({ role, createdBy, currentUserId: currentUser?.id });
  return {
    role,
    isGuest: isProjectGuestRole(role),
    canCreateWorkItem: canCreateWorkItemInProject(role),
    canGuestEdit,
    isGuestEditable: canGuestEdit && !archivedAt,
  };
};

/**
 * A guest can only delete attachments they uploaded themselves (the API allows delete for admins and the uploader).
 * Everyone else keeps the existing behaviour, so this only ever narrows the guest case.
 */
export const useCanDeleteWorkItemAttachment = (attachment: { issue_id: string; created_by: string } | undefined) => {
  const { workspaceSlug } = useParams();
  const { data: currentUser } = useUser();
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const projectId = attachment ? getIssueById(attachment.issue_id)?.project_id : undefined;
  const { isGuest } = useGuestWorkItemAccess(workspaceSlug?.toString(), projectId);
  return !isGuest || (!!attachment && attachment.created_by === currentUser?.id);
};
