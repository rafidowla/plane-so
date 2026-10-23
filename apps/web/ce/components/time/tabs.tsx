/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking-v2 — the Time area's tab list. Add an entry here to add a tab.
import type { ComponentType } from "react";
import { TimeDashboard } from "./dashboard/root";
import { ApprovalsTab } from "./approvals/root";
import { EstimateAccuracyTab } from "./estimate-accuracy/root"; // FORK: time-tracking-v2 S7
// FORK: time-tracking-v2 S5
import { ReportsTab } from "./reports/root";
import { SettingsTab } from "./settings/root";
import { TimesheetTab } from "./timesheet/root";

export type TTimeTab = { key: string; label: string; content: ComponentType };

type TTabOptions = {
  /** workspace admin or project admin: sees Approvals */
  canReview?: boolean;
  /** pending requests awaiting this reviewer, shown on the Approvals label */
  pendingCount?: number;
  /** FORK: time-tracking-v2 S5 — workspace admin: sees Settings */
  canAdminister?: boolean;
};

// FORK: time-tracking-v2 S4 — "My timesheet" holds the week grid, entries by date and My requests (S3b's
// standalone "My requests" tab is folded into it; a /time/requests/ link falls back to it in the page).
// "Approvals" is reviewer-only and carries the pending count.
export const getTimeTabs = ({
  canReview = false,
  pendingCount = 0,
  canAdminister = false,
}: TTabOptions = {}): TTimeTab[] => [
  { key: "dashboard", label: "Dashboard", content: TimeDashboard },
  { key: "timesheet", label: "My timesheet", content: TimesheetTab },
  // FORK: time-tracking-v2 S5 — Reports for every internal user (members see only themselves)
  { key: "reports", label: "Reports", content: ReportsTab },
  // FORK: time-tracking-v2 S7 — estimate accuracy; the server scopes rows by role (members see their own)
  { key: "estimates", label: "Estimates", content: EstimateAccuracyTab },
  ...(canReview
    ? [
        {
          key: "approvals",
          label: pendingCount > 0 ? `Approvals (${pendingCount > 99 ? "99+" : pendingCount})` : "Approvals",
          content: ApprovalsTab,
        },
      ]
    : []),
  // FORK: time-tracking-v2 S5 — Settings for workspace admins only
  ...(canAdminister ? [{ key: "settings", label: "Settings", content: SettingsTab }] : []),
];
