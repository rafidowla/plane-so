/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — the Time area's Reports tab. Members see only themselves; workspace and
// project admins get a team / person switch. Numbers come from `GET time/report/`; the CSV button
// downloads the entry-level export for the same range and person.
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Download } from "lucide-react";
import { Loader } from "@plane/blocks/skeleton";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { DateRangeFilter, getPresetRange, type TDateRangePreset } from "../../analytics/time/date-range-filter";
import { useCanReviewTime } from "../approvals/access";
import { ReportCharts } from "./charts";
import { canSwitchPerson, dateSpan, reportErrorMessage, classifyReportError, validateRange } from "./report-utils";
import { MembersTable, ProjectsTable, TasksTable, WeeksTable } from "./tables";
import { ReportTiles } from "./tiles";
import type { TTimeReportV2 } from "./types";

export const ReportsTab = observer(function ReportsTab() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  // Admins land on the team view; everyone else on their own. A 403 `forbidden_scope` falls back to "me".
  const canReview = useCanReviewTime(workspaceSlug);

  const [preset, setPreset] = useState<TDateRangePreset>("this_week");
  const [customRange, setCustomRange] = useState(() => getPresetRange("this_week"));
  const { startDate, endDate } = preset === "custom" ? customRange : getPresetRange(preset);
  const [picked, setPicked] = useState<string | null>(null);
  const target = picked ?? (canReview ? "team" : "me");
  const rangeError = validateRange(startDate, endDate);

  const { data, error, isLoading } = useSWR<TTimeReportV2>(
    workspaceSlug && !rangeError ? ["time-report-v2", workspaceSlug, startDate, endDate, target] : null,
    workspaceSlug && !rangeError
      ? () => timeTrackingService.getTimeReportV2(workspaceSlug, { from: startDate, to: endDate, user: target })
      : null,
    { keepPreviousData: true, revalidateOnFocus: false, shouldRetryOnError: false }
  );

  if (!workspaceSlug) return null;

  const switchable = canSwitchPerson(data?.scope) || (!data && canReview);
  const errorKind = error ? classifyReportError(error) : null;
  const csvHref = timeTrackingService.timeReportV2CsvUrl(workspaceSlug, { from: startDate, to: endDate, user: target });
  const personName = data?.user?.display_name;

  return (
    <div className="flex flex-col gap-4 px-4 py-4" data-testid="time-reports">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-16 font-semibold text-primary">
          {target === "team" ? "Team report" : switchable && personName ? `${personName}'s report` : "My report"}
        </h2>
        {switchable && (
          <select
            aria-label="Person"
            data-testid="report-person"
            value={target}
            onChange={(e) => setPicked(e.target.value)}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
          >
            <option value="team">Whole team</option>
            <option value="me">Me</option>
            {(data?.people ?? []).map((p) => (
              <option key={p.user_id} value={p.user_id}>
                {p.display_name}
                {p.is_tracked_now ? "" : " (not tracked)"}
              </option>
            ))}
          </select>
        )}
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
          {/* A plain link: the browser downloads it with the session cookie; no fetch needed. */}
          <a
            href={rangeError ? undefined : csvHref}
            aria-disabled={!!rangeError}
            download
            data-testid="report-csv"
            className="text-sm inline-flex items-center gap-1 rounded border border-subtle px-3 py-1 text-primary hover:bg-layer-transparent-hover"
          >
            <Download className="size-3.5" />
            Download CSV
          </a>
        </div>
      </div>

      {rangeError && (
        <div role="alert" data-testid="report-range-error" className="text-sm text-danger-primary">
          {rangeError}
        </div>
      )}

      {errorKind && (
        <div
          role="alert"
          data-testid="report-error"
          className="text-sm flex flex-wrap items-center gap-3 rounded-md border border-danger-subtle bg-danger-subtle px-3 py-2 text-danger-primary"
        >
          <span>{reportErrorMessage(error)}</span>
          {(errorKind === "forbidden_scope" || errorKind === "forbidden_user") && target !== "me" && (
            <button type="button" className="underline" onClick={() => setPicked("me")}>
              Show my report
            </button>
          )}
        </div>
      )}

      {!rangeError && !data && isLoading && (
        <Loader className="space-y-3">
          <Loader.Item height="72px" />
          <Loader.Item height="300px" />
          <Loader.Item height="160px" />
        </Loader>
      )}

      {data && !rangeError && (
        <>
          <p className="text-xs text-tertiary">
            {dateSpan(data.from, data.to)} · times in {data.timezone}
          </p>
          <ReportTiles tiles={data.tiles} billingEnabled={data.billing_enabled} showMembers={target === "team"} />
          <ReportCharts series={data.series} />
          {data.members && (
            <MembersTable
              members={data.members}
              truncated={!!data.members_truncated}
              billing={data.billing_enabled}
              onSelect={setPicked}
            />
          )}
          {data.weeks && <WeeksTable weeks={data.weeks} billing={data.billing_enabled} />}
          <ProjectsTable projects={data.projects} />
          <TasksTable tasks={data.tasks} workspaceSlug={workspaceSlug} />
        </>
      )}
    </div>
  );
});
