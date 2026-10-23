/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — the week grid: a row per project (subtotal) with its work items beneath,
// Monday to Sunday in the workspace time zone, a daily-total row against what was expected, and
// weekends shaded (a day is a weekend when it is not a work day in this workspace).
import { Fragment } from "react";
import { cn } from "@plane/utils";
import { describeBalance, formatCell, formatDayShort, paceOf } from "./timesheet-utils";
import type { TTimesheetWeek } from "./types";
import { formatLoggedMinutes } from "../../issues/worklog/timer-utils";
// FORK: time-tracking-v2 holidays/leave (PSR-122)
import { dayOffText, dayOffTooltip } from "../settings/holidays-utils";

type TProps = {
  week: TTimesheetWeek;
  /** members asking for time on a past day: shows the per-day "Request" button */
  canRequestDay: boolean;
  onRequestDay: (date: string) => void;
};

const PACE_CLASS = {
  none: "text-tertiary",
  under: "text-amber-600",
  met: "text-success-primary",
  over: "text-primary",
} as const;

export function TimesheetGrid({ week, canRequestDay, onRequestDay }: TProps) {
  const { days, projects, totals } = week;
  const hasOther = totals.other_projects > 0;
  const cell = "px-2 py-1.5 text-right tabular-nums";
  const dayShade = (i: number) => (days[i].is_weekend ? "bg-layer-1" : "");

  return (
    <div className="overflow-x-auto rounded-lg border border-subtle bg-surface-1" data-testid="timesheet-grid">
      <table className="text-sm w-full min-w-[720px] border-collapse">
        <thead>
          <tr className="border-b border-subtle text-tertiary">
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Work item
            </th>
            {days.map((d, i) => (
              <th
                key={d.date}
                scope="col"
                data-testid="timesheet-day-head"
                data-date={d.date}
                data-weekend={d.is_weekend}
                data-today={d.is_today}
                data-off={d.off ? d.off.kind : undefined}
                className={cn("px-2 py-2 text-right font-medium", dayShade(i), d.is_today && "text-primary")}
              >
                <div className={cn(d.is_today && "font-semibold")}>{formatDayShort(d.date)}</div>
                <div className="text-xs font-normal">
                  {d.expected_minutes > 0
                    ? formatLoggedMinutes(d.expected_minutes)
                    : d.off
                      ? "0h"
                      : d.is_weekend
                        ? "weekend"
                        : "–"}
                </div>
                {/* FORK: holidays/leave (PSR-122) — the server already took the day off from `expected_minutes` */}
                {d.off && (
                  <div
                    data-testid="timesheet-day-off"
                    data-date={d.date}
                    data-kind={d.off.kind}
                    data-portion={d.off.portion}
                    title={dayOffTooltip(d.off)}
                    className={cn(
                      "text-xs font-normal mt-0.5 inline-block rounded px-1.5 py-0.5 whitespace-nowrap",
                      d.off.kind === "holiday"
                        ? "bg-accent-subtle text-accent-primary"
                        : "border border-subtle bg-layer-1 text-secondary"
                    )}
                  >
                    {dayOffText(d.off)}
                  </div>
                )}
                {canRequestDay && !d.is_future && !d.is_today && d.tracked && (
                  <button
                    type="button"
                    data-testid="timesheet-request-day"
                    aria-label={`Request time for ${d.date}`}
                    className="text-xs font-normal text-accent-primary hover:underline"
                    onClick={() => onRequestDay(d.date)}
                  >
                    + Request
                  </button>
                )}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {projects.length === 0 && (
            <tr>
              <td colSpan={9} className="py-8 text-center text-tertiary" data-testid="timesheet-grid-empty">
                No time logged this week.
              </td>
            </tr>
          )}
          {projects.map((p) => (
            <Fragment key={p.project.id}>
              <tr className="border-b border-subtle bg-layer-1" data-testid="timesheet-project-row">
                <th scope="row" className="px-3 py-1.5 text-left font-semibold text-primary">
                  {p.project.name}
                  <span className="text-xs font-normal ml-2 text-tertiary">{p.project.identifier}</span>
                </th>
                {days.map((d, i) => (
                  <td key={d.date} className={cn(cell, "font-medium text-secondary")}>
                    {formatCell(p.minutes[i])}
                  </td>
                ))}
                <td className={cn(cell, "px-3 font-semibold text-primary")}>{formatCell(p.total)}</td>
              </tr>
              {p.issues.map((issue) => (
                <tr key={issue.id} className="border-b border-subtle last:border-b-0" data-testid="timesheet-issue-row">
                  <th scope="row" className="font-normal max-w-0 px-3 py-1.5 text-left">
                    <div className="flex min-w-0 items-center gap-2 pl-3">
                      <span className="shrink-0 text-secondary">{issue.ref}</span>
                      <span
                        className={cn("truncate", issue.is_deleted ? "text-tertiary line-through" : "text-primary")}
                      >
                        {issue.name}
                      </span>
                    </div>
                  </th>
                  {days.map((d, i) => (
                    <td
                      key={d.date}
                      className={cn(cell, dayShade(i), issue.minutes[i] > 0 ? "text-primary" : "text-tertiary")}
                    >
                      {formatCell(issue.minutes[i])}
                    </td>
                  ))}
                  <td className={cn(cell, "px-3 text-primary")}>{formatCell(issue.total)}</td>
                </tr>
              ))}
            </Fragment>
          ))}
          {hasOther && (
            <tr className="border-t border-subtle" data-testid="timesheet-other-row">
              <th scope="row" className="font-normal px-3 py-1.5 text-left text-tertiary">
                Other projects
              </th>
              {days.map((d, i) => (
                <td key={d.date} className={cn(cell, dayShade(i), "text-tertiary")}>
                  {formatCell(d.other_projects_minutes)}
                </td>
              ))}
              <td className={cn(cell, "px-3 text-tertiary")}>{formatCell(totals.other_projects)}</td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-subtle" data-testid="timesheet-total-row">
            <th scope="row" className="px-3 py-2 text-left font-semibold text-primary">
              Daily total
            </th>
            {days.map((d, i) => (
              <td
                key={d.date}
                data-testid="timesheet-day-total"
                data-date={d.date}
                className={cn(
                  cell,
                  dayShade(i),
                  "font-semibold",
                  PACE_CLASS[
                    d.is_future ? "none" : paceOf(d.logged_minutes + d.other_projects_minutes, d.expected_minutes)
                  ]
                )}
              >
                {formatCell(d.logged_minutes + d.other_projects_minutes)}
              </td>
            ))}
            <td className={cn(cell, "px-3 font-semibold text-primary")} data-testid="timesheet-week-total">
              {formatCell(totals.logged + totals.other_projects)}
            </td>
          </tr>
          <tr className="text-xs text-tertiary">
            <td colSpan={8} className="px-3 pb-2 text-right" data-testid="timesheet-balance">
              {describeBalance(totals.logged + totals.other_projects, totals.expected_to_date)}
            </td>
            <td className="px-3 pb-2 text-right" data-testid="timesheet-week-expected">
              of {formatLoggedMinutes(totals.expected)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
