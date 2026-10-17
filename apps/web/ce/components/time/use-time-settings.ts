/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — one SWR cache entry for the workspace's time settings, shared by the
// Settings tab and by anything that needs a switch from it (e.g. Analytics shows Timesheets only
// while weekly approval is on).
import useSWR from "swr";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTimeSettings } from "./reports/types";

export const TIME_SETTINGS_KEY = (workspaceSlug: string) => ["time-settings", workspaceSlug] as const;

export function useTimeSettings(workspaceSlug: string | undefined, enabled = true) {
  return useSWR<TTimeSettings>(
    enabled && workspaceSlug ? TIME_SETTINGS_KEY(workspaceSlug) : null,
    workspaceSlug ? () => timeTrackingService.getTimeSettings(workspaceSlug) : null,
    { revalidateOnFocus: false }
  );
}
