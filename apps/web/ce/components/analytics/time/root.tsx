/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useState } from "react";
import { Download } from "lucide-react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Button } from "@plane/propel/button";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import AnalyticsWrapper from "@/components/analytics/analytics-wrapper";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";

type TGroupBy = "resource" | "project" | "client";

const GROUPS: { key: TGroupBy; label: string }[] = [
  { key: "resource", label: "By resource" },
  { key: "project", label: "By project" },
  { key: "client", label: "By client" },
];

function hrs(minutes: number): string {
  return `${(minutes / 60).toFixed(2)}h`;
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export const TimeReport = observer(function TimeReport() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const [groupBy, setGroupBy] = useState<TGroupBy>("resource");
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(today());

  const params = { group_by: groupBy, start_date: startDate, end_date: endDate };
  const { data, isLoading } = useSWR(
    workspaceSlug ? `TIME_REPORT_${workspaceSlug}_${groupBy}_${startDate}_${endDate}` : null,
    workspaceSlug ? () => timeTrackingService.getTimeReport(workspaceSlug, params) : null
  );

  const groups = data?.groups ?? [];
  const totals = data?.totals;
  const isResource = groupBy === "resource";

  return (
    <AnalyticsWrapper i18nTitle="">
      <h1 className="mb-4 text-20 font-bold">Time report</h1>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-md border border-subtle p-0.5">
          {GROUPS.map((g) => (
            <button
              key={g.key}
              type="button"
              onClick={() => setGroupBy(g.key)}
              className={cn(
                "text-sm rounded px-3 py-1",
                groupBy === g.key ? "bg-surface-2 font-medium text-primary" : "text-tertiary hover:text-secondary"
              )}
            >
              {g.label}
            </button>
          ))}
        </div>
        <input
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
          className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
        />
        <span className="text-tertiary">→</span>
        <input
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
          className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
        />
        <a href={timeTrackingService.timeReportCsvUrl(workspaceSlug, params)} target="_blank" rel="noreferrer">
          <Button variant="secondary" size="sm">
            <Download className="mr-1 size-3" />
            Export CSV
          </Button>
        </a>
      </div>

      {isLoading ? (
        <Loader className="space-y-3">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : groups.length === 0 ? (
        <div className="text-sm py-12 text-center text-tertiary">No time logged in this range.</div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-subtle">
          <table className="text-sm w-full">
            <thead className="bg-surface-2 text-left text-tertiary">
              <tr>
                <th className="px-4 py-2 font-medium">{GROUPS.find((g) => g.key === groupBy)?.label.slice(3)}</th>
                <th className="px-4 py-2 text-right font-medium">Total</th>
                <th className="px-4 py-2 text-right font-medium">Billable</th>
                <th className="px-4 py-2 text-right font-medium">Non-billable</th>
                <th className="px-4 py-2 text-right font-medium">Amount</th>
                <th className="px-4 py-2 text-right font-medium">Entries</th>
                {isResource && <th className="px-4 py-2 text-right font-medium">Utilization</th>}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.key ?? g.name} className="border-t border-subtle">
                  <td className="px-4 py-2">{g.name}</td>
                  <td className="px-4 py-2 text-right">{hrs(g.total_minutes)}</td>
                  <td className="px-4 py-2 text-right">{hrs(g.billable_minutes)}</td>
                  <td className="px-4 py-2 text-right">{hrs(g.non_billable_minutes)}</td>
                  <td className="px-4 py-2 text-right">${g.billable_amount.toFixed(2)}</td>
                  <td className="px-4 py-2 text-right">{g.entry_count}</td>
                  {isResource && (
                    <td className="px-4 py-2 text-right">
                      {g.utilization_pct != null ? `${g.utilization_pct}%` : "—"}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
            {totals && (
              <tfoot className="border-t border-subtle bg-surface-2 font-medium">
                <tr>
                  <td className="px-4 py-2">Total</td>
                  <td className="px-4 py-2 text-right">{hrs(totals.total_minutes)}</td>
                  <td className="px-4 py-2 text-right">{hrs(totals.billable_minutes)}</td>
                  <td className="px-4 py-2 text-right">{hrs(totals.total_minutes - totals.billable_minutes)}</td>
                  <td className="px-4 py-2 text-right">${totals.billable_amount.toFixed(2)}</td>
                  <td className="px-4 py-2 text-right">{totals.entry_count}</td>
                  {isResource && <td className="px-4 py-2" />}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </AnalyticsWrapper>
  );
});
