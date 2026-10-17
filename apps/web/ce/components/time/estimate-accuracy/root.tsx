/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S7 — the Time area's Estimates tab: how close estimates are to the time actually
// logged on completed work. The server decides what each viewer may see (members: their own estimates).
import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { subDays } from "date-fns";
import { Loader } from "@plane/blocks/skeleton";
import { renderFormattedPayloadDate } from "@plane/utils";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import {
  formatAbsError,
  formatError,
  formatMinutes,
  formatRatio,
  formatShare,
  headline,
  scopeNote,
  type TTrendMetric,
} from "./accuracy-utils";
import { PeopleTable, ProjectsTable, TasksTable } from "./tables";
import { TrendChart } from "./trend-chart";
import type { TAccuracyGranularity } from "./types";

const RANGES = [
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
  { days: 180, label: "Last 180 days" },
  { days: 365, label: "Last 12 months" },
];

function excludedNote(excluded: { no_time: number; grandfathered: number; not_solo: number }, solo: boolean): string {
  const parts: string[] = [];
  if (excluded.no_time > 0) parts.push(`${excluded.no_time} with no time logged`);
  if (excluded.grandfathered > 0) parts.push(`${excluded.grandfathered} estimated after work had started`);
  if (solo && excluded.not_solo > 0) parts.push(`${excluded.not_solo} not solo work`);
  return parts.length ? ` Left out: ${parts.join(", ")}.` : "";
}

const CONTROL = "text-sm rounded border border-subtle bg-transparent px-2 py-1";

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-subtle bg-surface-1 px-4 py-3">
      <div className="text-xs text-tertiary">{label}</div>
      <div className="text-20 font-semibold text-primary">{value}</div>
      {hint && <div className="text-xs text-tertiary">{hint}</div>}
    </div>
  );
}

export const EstimateAccuracyTab = observer(function EstimateAccuracyTab() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";

  const [days, setDays] = useState(90);
  const [solo, setSolo] = useState(false);
  const [granularity, setGranularity] = useState<TAccuracyGranularity>("week");
  const [metric, setMetric] = useState<TTrendMetric>("ratio");

  const from = useMemo(() => renderFormattedPayloadDate(subDays(new Date(), days - 1)) ?? "", [days]);

  const { data, error, isLoading } = useSWR(
    workspaceSlug ? `TIME_ESTIMATE_ACCURACY_${workspaceSlug}_${from}_${solo}_${granularity}` : null,
    workspaceSlug ? () => timeTrackingService.getEstimateAccuracy(workspaceSlug, { from, solo, granularity }) : null,
    { keepPreviousData: true }
  );

  if (!workspaceSlug) return null;

  const withinPct = data?.thresholds.within_pct ?? 20;
  const summary = data?.summary;
  const read = data && summary ? headline(summary, withinPct) : null;

  return (
    <div className="flex flex-col gap-4 px-4 py-4" data-testid="estimate-accuracy">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="text-16 font-semibold text-primary">Estimate accuracy</h2>
          <p className="text-xs text-tertiary">
            {data ? scopeNote(data.scope) : "Estimates compared with time logged on completed work."}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <select
            aria-label="Date range"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className={CONTROL}
          >
            {RANGES.map((r) => (
              <option key={r.days} value={r.days}>
                {r.label}
              </option>
            ))}
          </select>
          <label
            className="text-sm flex items-center gap-1.5 text-secondary"
            title={`Only tasks where the estimator logged at least ${data?.thresholds.solo_pct ?? 80}% of the time`}
          >
            <input type="checkbox" checked={solo} onChange={(e) => setSolo(e.target.checked)} />
            Solo work only
          </label>
        </div>
      </div>

      {error && !data ? (
        <div className="text-sm py-12 text-center text-tertiary">Could not load estimate accuracy.</div>
      ) : isLoading && !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="72px" />
          <Loader.Item height="260px" />
          <Loader.Item height="160px" />
        </Loader>
      ) : data && summary ? (
        <>
          {read && <p className="text-sm text-secondary">{read}</p>}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="accuracy-summary">
            <Tile label="Completed tasks" value={String(summary.n)} hint={`from ${data.from} to ${data.to}`} />
            <Tile
              label="Actual vs estimate"
              value={formatRatio(summary.ratio)}
              hint={`${formatError(summary.error_pct)} · ${formatMinutes(summary.actual_minutes)} of ${formatMinutes(summary.original_minutes)}`}
            />
            <Tile
              label="Typical miss"
              value={formatAbsError(summary.median_abs_error_pct)}
              hint="median, either direction"
            />
            <Tile
              label={`Within ±${withinPct}%`}
              value={formatShare(summary.within_share)}
              hint={`${summary.within_count} of ${summary.n} tasks`}
            />
          </div>
          <p className="text-xs text-tertiary" data-testid="accuracy-excluded">
            Judged against the original estimate.
            {excludedNote(data.excluded, data.solo)}
          </p>

          <section className="rounded-lg border border-subtle bg-surface-1 px-4 py-3">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 className="text-14 font-semibold text-primary">Trend</h3>
              <div className="ml-auto flex flex-wrap items-center gap-3">
                <select
                  aria-label="Trend measure"
                  value={metric}
                  onChange={(e) => setMetric(e.target.value as TTrendMetric)}
                  className={CONTROL}
                >
                  <option value="ratio">Actual as % of estimate</option>
                  <option value="within">Share within the band</option>
                </select>
                <select
                  aria-label="Group by"
                  value={granularity}
                  onChange={(e) => setGranularity(e.target.value as TAccuracyGranularity)}
                  className={CONTROL}
                >
                  <option value="week">By week</option>
                  <option value="month">By month</option>
                </select>
              </div>
            </div>
            <TrendChart
              trend={data.trend}
              granularity={data.granularity}
              metric={metric}
              minSample={data.thresholds.min_sample}
            />
          </section>

          <ProjectsTable projects={data.projects} truncated={data.projects_truncated} withinPct={withinPct} />
          {data.scope !== "self" && (
            <PeopleTable people={data.people} truncated={data.people_truncated} withinPct={withinPct} />
          )}
          <TasksTable
            workspaceSlug={workspaceSlug}
            tasks={data.tasks.results}
            total={data.tasks.total}
            truncated={data.tasks.truncated}
            withinPct={withinPct}
          />
        </>
      ) : null}
    </div>
  );
});
