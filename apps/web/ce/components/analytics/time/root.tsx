/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { Fragment, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Download } from "lucide-react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { EStartOfTheWeek } from "@plane/types";
import { Button } from "@plane/propel/button";
import { Loader } from "@plane/ui";
import { cn, generateWorkItemLink } from "@plane/utils";
import AnalyticsWrapper from "@/components/analytics/analytics-wrapper";
import { MemberDropdown } from "@/components/dropdowns/member/dropdown";
import { useUserProfile } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { DateRangeFilter, getPresetRange, type TDateRangePreset } from "./date-range-filter";
import { TaskBreakdown } from "./task-breakdown";

type TGroupBy = "resource" | "project" | "client" | "issue";

const GROUPS: { key: TGroupBy; label: string; column: string }[] = [
  { key: "resource", label: "By resource", column: "Resource" },
  { key: "project", label: "By project", column: "Project" },
  { key: "client", label: "By client", column: "Client" },
  { key: "issue", label: "By task", column: "Task" },
];

export function hrs(minutes: number): string {
  return `${(minutes / 60).toFixed(2)}h`;
}

export const TimeReport = observer(function TimeReport() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { data: userProfile } = useUserProfile();
  const weekStartsOn = userProfile?.start_of_the_week ?? EStartOfTheWeek.MONDAY;

  const [groupBy, setGroupBy] = useState<TGroupBy>("resource");
  const [preset, setPreset] = useState<TDateRangePreset>("last_30");
  const [customRange, setCustomRange] = useState(() => getPresetRange("last_30"));
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const { startDate, endDate } = preset === "custom" ? customRange : getPresetRange(preset, weekStartsOn);

  const handleStartDateChange = (val: string) => {
    setCustomRange({ startDate: val, endDate });
    setPreset("custom");
  };
  const handleEndDateChange = (val: string) => {
    setCustomRange({ startDate, endDate: val });
    setPreset("custom");
  };

  const params: Record<string, string> = { group_by: groupBy, start_date: startDate, end_date: endDate };
  if (selectedUserIds.length > 0) params.user_ids = selectedUserIds.join(",");

  const { data, isLoading } = useSWR(
    workspaceSlug ? `TIME_REPORT_${workspaceSlug}_${JSON.stringify(params)}` : null,
    workspaceSlug ? () => timeTrackingService.getTimeReport(workspaceSlug, params) : null
  );

  const groups = data?.groups ?? [];
  const totals = data?.totals;
  const isResource = groupBy === "resource";
  const isIssueGroup = groupBy === "issue";

  // Auto-expand the single resource row when exactly one member is selected and
  // exactly one row comes back; reset expansion whenever filters or data change.
  useEffect(() => {
    if (isResource && selectedUserIds.length === 1 && groups.length === 1) {
      setExpandedKey(groups[0].key);
    } else {
      setExpandedKey(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupBy, startDate, endDate, selectedUserIds.join(","), groups.length, groups[0]?.key]);

  const toggleExpand = (key: string | null) => {
    if (!key) return;
    setExpandedKey((prev) => (prev === key ? null : key));
  };

  const columnCount = 1 + (isIssueGroup ? 1 : 0) + 5 + (isResource ? 2 : 0);

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
        <DateRangeFilter preset={preset} onChange={setPreset} />
        <input
          type="date"
          value={startDate}
          onChange={(e) => handleStartDateChange(e.target.value)}
          className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
        />
        <span className="text-tertiary">→</span>
        <input
          type="date"
          value={endDate}
          onChange={(e) => handleEndDateChange(e.target.value)}
          className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
        />
        <MemberDropdown
          value={selectedUserIds}
          onChange={setSelectedUserIds}
          multiple
          buttonVariant="border-with-text"
          showUserDetails
          placeholder="All members"
        />
        <a href={timeTrackingService.timeReportCsvUrl(workspaceSlug, params)} target="_blank" rel="noreferrer">
          <Button variant="secondary" size="sm">
            <Download className="mr-1 size-3" />
            Export CSV
          </Button>
        </a>
      </div>

      {data?.truncated && (
        <div className="text-xs mb-2 text-tertiary">
          Showing the top {data.limit} tasks by time logged — totals below still reflect the full range. Export CSV for
          the full list.
        </div>
      )}

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
                {isResource && <th className="w-8 px-2 py-2" />}
                <th className="px-4 py-2 font-medium">{GROUPS.find((g) => g.key === groupBy)?.column}</th>
                {isIssueGroup && <th className="px-4 py-2 font-medium">Project</th>}
                <th className="px-4 py-2 text-right font-medium">Total</th>
                <th className="px-4 py-2 text-right font-medium">Billable</th>
                <th className="px-4 py-2 text-right font-medium">Non-billable</th>
                <th className="px-4 py-2 text-right font-medium">Amount</th>
                <th className="px-4 py-2 text-right font-medium">Entries</th>
                {isResource && <th className="px-4 py-2 text-right font-medium">Utilization</th>}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const isExpanded = isResource && expandedKey === g.key;
                const workItemLink = isIssueGroup
                  ? generateWorkItemLink({
                      workspaceSlug,
                      projectId: g.project_id,
                      issueId: g.issue_id,
                      projectIdentifier: g.project_identifier,
                      sequenceId: g.sequence_id,
                    })
                  : null;
                return (
                  <Fragment key={g.key ?? g.name}>
                    <tr className="border-t border-subtle">
                      {isResource && (
                        <td className="px-2 py-2">
                          <button
                            type="button"
                            onClick={() => toggleExpand(g.key)}
                            className="text-tertiary hover:text-secondary"
                            aria-label={isExpanded ? "Collapse" : "Expand"}
                          >
                            {isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                          </button>
                        </td>
                      )}
                      <td className="px-4 py-2">
                        {workItemLink ? (
                          <a href={workItemLink} className="hover:underline">
                            {g.name}
                          </a>
                        ) : (
                          g.name
                        )}
                      </td>
                      {isIssueGroup && <td className="px-4 py-2 text-tertiary">{g.project_name ?? "—"}</td>}
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
                    {isExpanded && g.key && (
                      <tr className="border-t border-subtle bg-surface-1">
                        <td colSpan={columnCount} className="p-0">
                          <TaskBreakdown
                            workspaceSlug={workspaceSlug}
                            userIds={[g.key]}
                            startDate={startDate}
                            endDate={endDate}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
            {totals && (
              <tfoot className="border-t border-subtle bg-surface-2 font-medium">
                <tr>
                  {isResource && <td className="px-2 py-2" />}
                  <td className="px-4 py-2">Total</td>
                  {isIssueGroup && <td className="px-4 py-2" />}
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
