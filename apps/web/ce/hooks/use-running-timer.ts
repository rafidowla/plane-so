/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-105 — the signed-in user's running timer, read from the server so it
// survives navigation and reloads. Shared (one SWR key) by the indicator and by the
// worklog widget, which revalidates it after start/stop.
import { useMemo, useEffect, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TRunningTimer } from "@/plane-web/components/issues/worklog/types";
import { getElapsedSeconds, getServerOffsetMs } from "@/plane-web/components/issues/worklog/timer-utils";

export const runningTimerKey = (workspaceSlug: string) => `WORKSPACE_RUNNING_TIMER_${workspaceSlug}`;

/** Re-fetch the indicator's timer (call after anything that starts/stops a timer). */
export const revalidateRunningTimer = (workspaceSlug: string) => globalMutate(runningTimerKey(workspaceSlug));

export function useRunningTimer(workspaceSlug: string | undefined) {
  const { data, mutate, isLoading } = useSWR<TRunningTimer>(
    workspaceSlug ? runningTimerKey(workspaceSlug) : null,
    workspaceSlug ? () => timeTrackingService.getRunningTimer(workspaceSlug) : null,
    {
      // Pick up timers started/stopped in another tab or device without a reload.
      refreshInterval: 60_000,
      revalidateOnFocus: true,
      // A failed poll must not make the indicator vanish while a timer is running.
      keepPreviousData: true,
    }
  );

  const timer = data?.id ? data : undefined;
  // Offset between the server and browser clocks, fixed at the time of each fetch.
  const serverOffsetMs = useMemo(
    () => getServerOffsetMs(data?.server_now, Date.now()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data]
  );

  return { timer, serverOffsetMs, isLoading, mutate };
}

/** Elapsed whole seconds for a running timer, ticking once a second while it runs. */
export function useTimerElapsed(startedAt: string | undefined, serverOffsetMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return getElapsedSeconds(startedAt, now, serverOffsetMs);
}
