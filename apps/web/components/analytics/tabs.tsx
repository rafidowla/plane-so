/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { AnalyticsTab } from "@plane/types";
import { Overview } from "@/components/analytics/overview";
import { WorkItems } from "@/components/analytics/work-items";
// FORK: time-tracking — these implementations live in ce/ (fork-owned, upstream
// never had them); only this tabs.tsx file itself moved to core/ in the merge.
import { Clients } from "@/plane-web/components/analytics/clients/root";
import { Imports } from "@/plane-web/components/analytics/imports/root";
import { Timesheets } from "@/plane-web/components/analytics/timesheets/root";

// FORK: time-tracking-v2 S5 — the old "Time" tab moved to the Time area (Reports); "Timesheets" only
// exists while weekly approval is on.
export const getAnalyticsTabs = (
  t: (key: string, params?: Record<string, any>) => string,
  { showTimesheets = false }: { showTimesheets?: boolean } = {}
): AnalyticsTab[] =>
  [
    { key: "overview", label: t("common.overview"), content: Overview, isDisabled: false },
    { key: "work-items", label: t("sidebar.work_items"), content: WorkItems, isDisabled: false },
    // FORK: time-tracking
    ...(showTimesheets ? [{ key: "timesheets", label: "Timesheets", content: Timesheets, isDisabled: false }] : []),
    { key: "clients", label: "Clients", content: Clients, isDisabled: false },
    { key: "imports", label: "Imports", content: Imports, isDisabled: false },
  ] as AnalyticsTab[];
