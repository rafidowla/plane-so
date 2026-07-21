/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { AnalyticsTab } from "@plane/types";
import { Overview } from "@/components/analytics/overview";
import { WorkItems } from "@/components/analytics/work-items";
// FORK: time-tracking
import { Clients } from "./clients/root";
import { Imports } from "./imports/root";
import { TimeReport } from "./time/root";
import { Timesheets } from "./timesheets/root";

export const getAnalyticsTabs = (t: (key: string, params?: Record<string, any>) => string): AnalyticsTab[] =>
  [
    { key: "overview", label: t("common.overview"), content: Overview, isDisabled: false },
    { key: "work-items", label: t("sidebar.work_items"), content: WorkItems, isDisabled: false },
    // FORK: time-tracking
    { key: "time", label: "Time", content: TimeReport, isDisabled: false },
    { key: "timesheets", label: "Timesheets", content: Timesheets, isDisabled: false },
    { key: "clients", label: "Clients", content: Clients, isDisabled: false },
    { key: "imports", label: "Imports", content: Imports, isDisabled: false },
  ] as AnalyticsTab[];
