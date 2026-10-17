/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — who sees the Approvals tab, and the pending count on its badge.
import useSWR from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useUserPermissions } from "@/hooks/store/user";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { hasAdminProject } from "./roles";
import { PENDING_COUNT_KEY } from "../requests/request-keys";

/** Badge refresh cadence; new requests also arrive as notifications. */
export const PENDING_COUNT_REFRESH_MS = 60_000;

/** Workspace admins and project admins review requests; members and guests never see the tab. */
export function useCanReviewTime(workspaceSlug: string): boolean {
  const { allowPermissions, getProjectRolesByWorkspaceSlug } = useUserPermissions();
  if (!workspaceSlug) return false;
  return (
    allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE) ||
    hasAdminProject(getProjectRolesByWorkspaceSlug(workspaceSlug))
  );
}

/** Pending requests the signed-in reviewer can act on (0 for non-reviewers: no request is made). */
export function usePendingApprovalCount(workspaceSlug: string, enabled: boolean): number {
  const { data } = useSWR(
    enabled && workspaceSlug ? PENDING_COUNT_KEY(workspaceSlug) : null,
    () => timeTrackingService.getTimeRequests(workspaceSlug, { scope: "review", status: "pending", limit: 1 }),
    { refreshInterval: PENDING_COUNT_REFRESH_MS, refreshWhenHidden: false }
  );
  return data?.count ?? 0;
}
