/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — the Time area's Dashboard tab: your timer bar on top, then a live list of
// who is tracking what. The list polls every 5 s (paused while the tab is hidden) and ticks
// per second in between, using the server clock offset so a skewed browser clock doesn't show.
import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Loader } from "@plane/blocks/skeleton";
import { EStartOfTheWeek } from "@plane/types";
import { useUserProfile } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { DateRangeFilter, getPresetRange, type TDateRangePreset } from "../../analytics/time/date-range-filter";
import { getServerOffsetMs } from "../../issues/worklog/timer-utils";
import { TimerBar } from "../timer/timer-bar";
import { liveKeyPrefix } from "../timer/timer-actions";
import { LiveRow } from "./live-row";

/** Live data refresh cadence (PO decision: 5 s, nothing while the tab is hidden). */
export const LIVE_REFRESH_MS = 5_000;

export const TimeDashboard = observer(function TimeDashboard() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { data: userProfile } = useUserProfile();
  const weekStartsOn = userProfile?.start_of_the_week ?? EStartOfTheWeek.MONDAY;

  const [preset, setPreset] = useState<TDateRangePreset>("this_week");
  const [customRange, setCustomRange] = useState(() => getPresetRange("this_week"));
  const { startDate, endDate } = preset === "custom" ? customRange : getPresetRange(preset, weekStartsOn);

  const { data, isLoading } = useSWR(
    workspaceSlug ? `${liveKeyPrefix(workspaceSlug)}_${startDate}_${endDate}` : null,
    workspaceSlug ? () => timeTrackingService.getLive(workspaceSlug, startDate, endDate) : null,
    { refreshInterval: LIVE_REFRESH_MS, refreshWhenHidden: false, keepPreviousData: true }
  );

  // Clock offset and arrival time are fixed per response; `now` ticks between responses.
  const { serverOffsetMs, fetchedAtMs } = useMemo(() => {
    const at = Date.now();
    return { serverOffsetMs: getServerOffsetMs(data?.server_now, at), fetchedAtMs: at };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Running people first; otherwise keep the server's order.
  const rows = useMemo(() => {
    const list = data?.rows ?? [];
    return [...list.filter((r) => r.status === "running"), ...list.filter((r) => r.status !== "running")];
  }, [data]);

  if (!workspaceSlug) return null;

  return (
    <div className="flex flex-col gap-2 px-4 py-4" data-testid="time-dashboard">
      <TimerBar workspaceSlug={workspaceSlug} />

      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-16 font-semibold text-primary">Who's tracking</h2>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <DateRangeFilter preset={preset} onChange={setPreset} />
          <input
            type="date"
            aria-label="Start date"
            value={startDate}
            onChange={(e) => {
              setCustomRange({ startDate: e.target.value, endDate });
              setPreset("custom");
            }}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
          />
          <span className="text-tertiary">→</span>
          <input
            type="date"
            aria-label="End date"
            value={endDate}
            onChange={(e) => {
              setCustomRange({ startDate, endDate: e.target.value });
              setPreset("custom");
            }}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
          />
        </div>
      </div>

      {isLoading && !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="48px" />
          <Loader.Item height="48px" />
          <Loader.Item height="48px" />
        </Loader>
      ) : rows.length === 0 ? (
        <div className="text-sm py-12 text-center text-tertiary">Nobody is tracking time yet.</div>
      ) : (
        <div className="rounded-lg border border-subtle bg-surface-1" data-testid="live-rows">
          {rows.map((row) => (
            <LiveRow
              key={row.user.id}
              row={row}
              workspaceSlug={workspaceSlug}
              nowMs={nowMs}
              serverOffsetMs={serverOffsetMs}
              fetchedAtMs={fetchedAtMs}
            />
          ))}
        </div>
      )}
    </div>
  );
});
