/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — entries grouped by date, then by work item: a count badge when a task has
// several entries, the span they cover, the total, and each entry with the one action it allows.
import Link from "next/link";
import { Pencil, X } from "lucide-react";
import { generateWorkItemLink } from "@plane/utils";
import { formatLoggedMinutes } from "../../issues/worklog/timer-utils";
import type { TWorklog, TWorklogSource } from "../../issues/worklog/types";
import { formatDayLong, formatSpan, entryActionFor, groupBadge } from "./timesheet-utils";
import type { TTimesheetEntry, TTimesheetGroup, TTimesheetWeek } from "./types";

type TProps = {
  week: TTimesheetWeek;
  onEdit: (entry: TWorklog, direct: boolean) => void;
  onDelete: (entry: TWorklog, direct: boolean) => void;
};

/** The few fields the request modal reads, from a timesheet entry. */
export const toWorklog = (entry: TTimesheetEntry): TWorklog => ({
  id: entry.id,
  issue: entry.issue_id,
  project: entry.project_id,
  workspace: "",
  logged_by: "",
  duration: entry.duration,
  description: entry.description,
  logged_date: entry.logged_date,
  started_at: entry.started_at,
  ended_at: entry.ended_at,
  source: (entry.source === "timer" ? "timer" : "manual") as TWorklogSource,
  work_type: entry.work_type,
  is_billable: false,
  currency: "",
  approval_status: entry.approval_status as TWorklog["approval_status"],
  is_locked: entry.is_locked,
  created_by: "",
  created_at: "",
});

const Tag = ({ children, testId }: { children: string; testId?: string }) => (
  <span
    data-testid={testId}
    className="text-xs rounded border border-subtle bg-layer-1 px-1.5 py-0.5 leading-none text-tertiary"
  >
    {children}
  </span>
);

function EntryRow({ week, entry, onEdit, onDelete }: TProps & { entry: TTimesheetEntry }) {
  const action = entryActionFor(week, entry);
  const direct = action.kind === "edit";
  const span = formatSpan(entry.started_at, entry.ended_at, week.timezone);
  const w = toWorklog(entry);
  const verb = direct ? "Edit time" : "Request a time change";
  const delVerb = direct ? "Delete time entry" : "Request deletion";
  return (
    <div
      data-testid="timesheet-entry"
      data-entry-id={entry.id}
      className="text-sm flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 pr-3 pl-6"
    >
      <span className="w-28 shrink-0 text-secondary tabular-nums">{span || "No clock times"}</span>
      <span className="w-16 shrink-0 text-primary tabular-nums">{formatLoggedMinutes(entry.duration)}</span>
      <span className="min-w-0 flex-1 truncate text-tertiary">{entry.description}</span>
      <span className="flex items-center gap-1.5">
        {entry.source === "manual" && <Tag>Manual</Tag>}
        {entry.auto_stopped && <Tag>Auto-stopped</Tag>}
        {entry.idle_stopped && <Tag>Idle</Tag>}
        {entry.pending_request_id && <Tag testId="timesheet-pending-badge">Change pending</Tag>}
        {entry.is_locked && <Tag>Locked</Tag>}
      </span>
      {action.kind !== "none" ? (
        <span className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            data-testid="timesheet-entry-edit"
            aria-label={verb}
            title={verb}
            className="text-tertiary hover:text-primary"
            onClick={() => onEdit(w, direct)}
          >
            <Pencil className="size-3" />
          </button>
          <button
            type="button"
            data-testid="timesheet-entry-delete"
            aria-label={delVerb}
            title={delVerb}
            className="hover:text-danger text-tertiary"
            onClick={() => onDelete(w, direct)}
          >
            <X className="size-3.5" />
          </button>
        </span>
      ) : (
        action.reason && <span className="text-xs shrink-0 text-tertiary">{action.reason}</span>
      )}
    </div>
  );
}

/** "PP-12" -> link to that work item (the ref is a snapshot, so a deleted item has no link). */
function linkFor(workspaceSlug: string, group: TTimesheetGroup): string | null {
  if (group.issue.is_deleted) return null;
  const cut = group.issue.ref.lastIndexOf("-");
  const sequenceId = Number(group.issue.ref.slice(cut + 1));
  if (cut < 1 || !Number.isInteger(sequenceId)) return null;
  return generateWorkItemLink({
    workspaceSlug,
    projectId: group.project_id,
    issueId: group.issue.id,
    projectIdentifier: group.issue.ref.slice(0, cut),
    sequenceId,
  });
}

function GroupBlock({
  week,
  group,
  onEdit,
  onDelete,
  workspaceSlug,
}: TProps & { group: TTimesheetGroup; workspaceSlug: string }) {
  const href = linkFor(workspaceSlug, group);
  const badge = groupBadge(group.count);
  const span = formatSpan(group.span_started_at, group.span_ended_at, week.timezone);
  return (
    <div
      data-testid="timesheet-group"
      data-issue-id={group.issue.id}
      className="border-b border-subtle last:border-b-0"
    >
      <div className="text-sm flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        {href ? (
          <Link href={href} className="flex min-w-0 flex-1 items-center gap-3 hover:underline">
            <span className="shrink-0 font-medium text-secondary">{group.issue.ref}</span>
            <span className="min-w-0 truncate text-primary">{group.issue.name}</span>
          </Link>
        ) : (
          <span className="flex min-w-0 flex-1 items-center gap-3">
            <span className="shrink-0 font-medium text-secondary">{group.issue.ref}</span>
            <span className="min-w-0 truncate text-tertiary line-through">{group.issue.name}</span>
          </span>
        )}
        {badge && (
          <span
            data-testid="timesheet-group-badge"
            title={`${group.count} entries`}
            className="text-xs rounded-full bg-layer-1 px-2 py-0.5 font-medium text-secondary"
          >
            {badge}
          </span>
        )}
        {span && <span className="text-xs text-tertiary tabular-nums">{span}</span>}
        <span data-testid="timesheet-group-total" className="shrink-0 font-medium text-primary tabular-nums">
          {formatLoggedMinutes(group.total_minutes)}
        </span>
      </div>
      <div className="pb-1">
        {group.entries.map((entry) => (
          <EntryRow key={entry.id} week={week} entry={entry} onEdit={onEdit} onDelete={onDelete} />
        ))}
      </div>
    </div>
  );
}

export function TimesheetEntries({ week, onEdit, onDelete, workspaceSlug }: TProps & { workspaceSlug: string }) {
  if (week.entries_by_date.length === 0) {
    return (
      <div className="text-sm py-8 text-center text-tertiary" data-testid="timesheet-entries-empty">
        No entries this week.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3" data-testid="timesheet-entries">
      {week.entries_by_date.map((day) => (
        <section
          key={day.date}
          data-testid="timesheet-date"
          data-date={day.date}
          className="rounded-lg border border-subtle bg-surface-1"
        >
          <header className="text-sm flex items-center gap-3 border-b border-subtle bg-layer-1 px-3 py-2">
            <h3 className="font-semibold text-primary">{formatDayLong(day.date)}</h3>
            <span className="ml-auto font-semibold text-primary tabular-nums" data-testid="timesheet-date-total">
              {formatLoggedMinutes(day.total_minutes)}
            </span>
          </header>
          {day.groups.map((group) => (
            <GroupBlock
              key={group.issue.id}
              week={week}
              group={group}
              workspaceSlug={workspaceSlug}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </section>
      ))}
      {week.entries_truncated && (
        <p className="text-xs text-center text-tertiary" data-testid="timesheet-truncated">
          This week has more entries than can be shown. The grid above still counts all of them.
        </p>
      )}
    </div>
  );
}
