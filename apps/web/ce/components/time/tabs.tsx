/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking-v2 — the Time area's tab list. Add an entry here to add a tab.
import type { ComponentType } from "react";
import { TimeDashboard } from "./dashboard/root";
import { ApprovalsTab } from "./approvals/root";
import { MyRequests } from "./requests/my-requests";

export type TTimeTab = { key: string; label: string; content: ComponentType };

type TTabOptions = {
  /** workspace admin or project admin: sees Approvals */
  canReview?: boolean;
  /** pending requests awaiting this reviewer, shown on the Approvals label */
  pendingCount?: number;
};

// FORK: time-tracking-v2 S3b — "My requests" stands in for the My timesheet area (S4) until it exists;
// "Approvals" is reviewer-only and carries the pending count.
export const getTimeTabs = ({ canReview = false, pendingCount = 0 }: TTabOptions = {}): TTimeTab[] => [
  { key: "dashboard", label: "Dashboard", content: TimeDashboard },
  { key: "requests", label: "My requests", content: MyRequests },
  ...(canReview
    ? [
        {
          key: "approvals",
          label: pendingCount > 0 ? `Approvals (${pendingCount > 99 ? "99+" : pendingCount})` : "Approvals",
          content: ApprovalsTab,
        },
      ]
    : []),
];
