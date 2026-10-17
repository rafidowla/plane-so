/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking-v2 — the Time area's tab list. Add an entry here to add a tab.
import type { ComponentType } from "react";
import { TimeDashboard } from "./dashboard/root";
import { ApprovalsTab } from "./approvals/root";
import { TimesheetTab } from "./timesheet/root";

export type TTimeTab = { key: string; label: string; content: ComponentType };

type TTabOptions = {
  /** workspace admin or project admin: sees Approvals */
  canReview?: boolean;
  /** pending requests awaiting this reviewer, shown on the Approvals label */
  pendingCount?: number;
};

// FORK: time-tracking-v2 S4 — "My timesheet" holds the week grid, entries by date and My requests (S3b's
// standalone "My requests" tab is folded into it; a /time/requests/ link falls back to it in the page).
// "Approvals" is reviewer-only and carries the pending count.
export const getTimeTabs = ({ canReview = false, pendingCount = 0 }: TTabOptions = {}): TTimeTab[] => [
  { key: "dashboard", label: "Dashboard", content: TimeDashboard },
  { key: "timesheet", label: "My timesheet", content: TimesheetTab },
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
