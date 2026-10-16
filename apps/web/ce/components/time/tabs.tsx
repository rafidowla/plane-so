/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking-v2 — the Time area's tab list. Add an entry here to add a tab.
import type { ComponentType } from "react";
import { TimeDashboard } from "./dashboard/root";

export type TTimeTab = { key: string; label: string; content: ComponentType };

export const getTimeTabs = (): TTimeTab[] => [{ key: "dashboard", label: "Dashboard", content: TimeDashboard }];
