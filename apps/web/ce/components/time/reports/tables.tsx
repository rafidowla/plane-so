/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — breakdown tables of the Reports tab: team members, weeks (one person),
// projects (with an "Other projects" row) and tasks.
import Link from "next/link";
import { cn } from "@plane/utils";
import { balanceTone, dateSpan, formatHm, formatHours, formatSignedHours, truncateTitle } from "./report-utils";
// FORK: time-tracking-v2 holidays/leave (PSR-122)
import { describeDaysOff, formatDaysOff, offHint } from "../settings/holidays-utils";
import type { TTimeReportV2 } from "./types";

const TH = "px-3 py-2 text-left text-xs font-medium text-tertiary";
const TD = "px-3 py-2 text-sm text-primary";
const NUM = "text-right tabular-nums";

function Section({
  title,
  testId,
  note,
  children,
}: {
  title: string;
  testId: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2" data-testid={testId}>
      <div className="flex items-baseline gap-3">
        <h3 className="text-16 font-semibold text-primary">{title}</h3>
        {note && <span className="text-xs text-tertiary">{note}</span>}
      </div>
      <div className="overflow-x-auto rounded-lg border border-subtle bg-surface-1">{children}</div>
    </section>
  );
}

function BalanceCell({ minutes }: { minutes: number }) {
  const tone = balanceTone(minutes);
  return (
    <td
      className={cn(TD, NUM, {
        "text-success-primary": tone === "positive",
        "text-danger-primary": tone === "negative",
      })}
    >
      {formatSignedHours(minutes)}
    </td>
  );
}

type TMembersProps = {
  members: NonNullable<TTimeReportV2["members"]>;
  truncated: boolean;
  billing: boolean;
  onSelect: (userId: string) => void;
};

export function MembersTable({ members, truncated, billing, onSelect }: TMembersProps) {
  return (
    <Section
      title="By person"
      testId="report-members"
      note={truncated ? "Showing the first people only; narrow the range or pick a person." : undefined}
    >
      <table className="w-full">
        <thead>
          <tr>
            <th className={TH}>Person</th>
            <th className={cn(TH, NUM)}>Logged</th>
            <th className={cn(TH, NUM)}>Expected to date</th>
            <th className={cn(TH, NUM)}>Balance</th>
            <th className={cn(TH, NUM)}>Weekend</th>
            <th className={cn(TH, NUM)}>Days off</th>
            <th className={cn(TH, NUM)}>Weekly capacity</th>
            {billing && <th className={cn(TH, NUM)}>Billable</th>}
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id} className="border-t border-subtle" data-testid="report-member-row">
              <td className={TD}>
                <button
                  type="button"
                  className="text-left text-accent-primary hover:underline"
                  onClick={() => onSelect(m.user_id)}
                >
                  {m.display_name}
                </button>
                {!m.is_tracked_now && <span className="text-xs ml-2 text-tertiary">not tracked</span>}
              </td>
              <td className={cn(TD, NUM)}>{formatHours(m.logged_minutes)}</td>
              <td className={cn(TD, NUM)}>{formatHours(m.expected_to_date_minutes)}</td>
              <BalanceCell minutes={m.balance_to_date_minutes} />
              <td className={cn(TD, NUM)}>{formatHours(m.weekend_minutes)}</td>
              <td
                className={cn(TD, NUM)}
                data-testid="report-member-days-off"
                title={describeDaysOff(m.days_off) || undefined}
              >
                {formatDaysOff(m.days_off)}
              </td>
              <td className={cn(TD, NUM)}>
                {m.weekly_capacity_minutes === null ? "Work week" : formatHm(m.weekly_capacity_minutes)}
              </td>
              {billing && <td className={cn(TD, NUM)}>{formatHours(m.billable_minutes)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function WeeksTable({ weeks, billing }: { weeks: NonNullable<TTimeReportV2["weeks"]>; billing: boolean }) {
  return (
    <Section title="By week" testId="report-weeks">
      <table className="w-full">
        <thead>
          <tr>
            <th className={TH}>Week</th>
            <th className={cn(TH, NUM)}>Logged</th>
            <th className={cn(TH, NUM)}>Expected</th>
            <th className={cn(TH, NUM)}>Balance</th>
            <th className={cn(TH, NUM)}>Weekend</th>
            {billing && <th className={cn(TH, NUM)}>Billable</th>}
          </tr>
        </thead>
        <tbody>
          {weeks.map((w) => (
            <tr key={w.week_start} className="border-t border-subtle" data-testid="report-week-row">
              <td className={TD}>{dateSpan(w.from, w.to)}</td>
              <td className={cn(TD, NUM)}>{formatHours(w.logged_minutes)}</td>
              <td className={cn(TD, NUM)}>
                {formatHours(w.expected_minutes)}
                {offHint(w.off_minutes) && (
                  <span className="text-xs ml-2 text-tertiary" data-testid="report-week-off">
                    {offHint(w.off_minutes)}
                  </span>
                )}
              </td>
              <BalanceCell minutes={w.balance_to_date_minutes} />
              <td className={cn(TD, NUM)}>{formatHours(w.weekend_minutes)}</td>
              {billing && <td className={cn(TD, NUM)}>{formatHours(w.billable_minutes)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function ProjectsTable({ projects }: { projects: TTimeReportV2["projects"] }) {
  const other = projects.other_projects;
  return (
    <Section title="By project" testId="report-projects">
      {projects.results.length === 0 && other.logged_minutes === 0 ? (
        <p className="text-sm px-3 py-4 text-tertiary">No time logged in this range.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr>
              <th className={TH}>Project</th>
              <th className={cn(TH, NUM)}>Logged</th>
              <th className={cn(TH, NUM)}>Entries</th>
            </tr>
          </thead>
          <tbody>
            {projects.results.map((p) => (
              <tr key={p.project_id} className="border-t border-subtle" data-testid="report-project-row">
                <td className={TD}>
                  <span className="text-xs mr-2 text-tertiary">{p.identifier}</span>
                  {p.name}
                </td>
                <td className={cn(TD, NUM)}>{formatHours(p.logged_minutes)}</td>
                <td className={cn(TD, NUM)}>{p.entry_count}</td>
              </tr>
            ))}
            {other.logged_minutes > 0 && (
              <tr className="border-t border-subtle" data-testid="report-other-projects">
                <td className={cn(TD, "text-secondary italic")}>{other.label}</td>
                <td className={cn(TD, NUM)}>{formatHours(other.logged_minutes)}</td>
                <td className={cn(TD, NUM)}>–</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Section>
  );
}

export function TasksTable({ tasks, workspaceSlug }: { tasks: TTimeReportV2["tasks"]; workspaceSlug: string }) {
  return (
    <Section
      title="By task"
      testId="report-tasks"
      note={tasks.truncated ? `Top ${tasks.limit} tasks by time; the rest are not listed.` : undefined}
    >
      {tasks.results.length === 0 && tasks.other_projects_minutes === 0 ? (
        <p className="text-sm px-3 py-4 text-tertiary">No time logged in this range.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr>
              <th className={TH}>Task</th>
              <th className={TH}>Project</th>
              <th className={cn(TH, NUM)}>Logged</th>
              <th className={cn(TH, NUM)}>Entries</th>
            </tr>
          </thead>
          <tbody>
            {tasks.results.map((t) => {
              const key = t.project_identifier && t.sequence_id ? `${t.project_identifier}-${t.sequence_id}` : null;
              return (
                <tr
                  key={`${t.issue_id ?? `name:${t.name}`}|${t.project_id ?? ""}`}
                  className="border-t border-subtle"
                  data-testid="report-task-row"
                >
                  <td className={TD} title={t.name}>
                    {/* a deleted task's name already carries its key and a "(deleted)" prefix from the server */}
                    {key && !t.is_deleted && <span className="text-xs mr-2 text-tertiary">{key}</span>}
                    {t.issue_id && !t.is_deleted && key ? (
                      <Link href={`/${workspaceSlug}/browse/${key}/`} className="hover:underline">
                        {truncateTitle(t.name)}
                      </Link>
                    ) : (
                      truncateTitle(t.name)
                    )}
                  </td>
                  <td className={cn(TD, "text-secondary")}>{t.project_name ?? "–"}</td>
                  <td className={cn(TD, NUM)}>{formatHours(t.logged_minutes)}</td>
                  <td className={cn(TD, NUM)}>{t.entry_count}</td>
                </tr>
              );
            })}
            {tasks.other_projects_minutes > 0 && (
              <tr className="border-t border-subtle" data-testid="report-tasks-other">
                <td className={cn(TD, "text-secondary italic")}>Other projects</td>
                <td className={TD}>–</td>
                <td className={cn(TD, NUM)}>{formatHours(tasks.other_projects_minutes)}</td>
                <td className={cn(TD, NUM)}>–</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Section>
  );
}
