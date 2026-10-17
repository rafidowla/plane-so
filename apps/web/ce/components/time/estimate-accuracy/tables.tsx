/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S7 — the Tasks, Projects and People tables of the Estimates tab.
import Link from "next/link";
import { generateWorkItemLink } from "@plane/utils";
import {
  errorTone,
  formatAbsError,
  formatError,
  formatMinutes,
  formatRatio,
  formatShare,
  type TErrorTone,
} from "./accuracy-utils";
import type { TAccuracyPerson, TAccuracyProject, TAccuracyStats, TAccuracyTask } from "./types";

const TONE_CLASS: Record<TErrorTone, string> = {
  good: "bg-success-subtle text-success-primary",
  over: "bg-danger-subtle text-danger-primary",
  under: "bg-warning-subtle text-warning-primary",
  none: "text-tertiary",
};

function ErrorBadge({ errorPct, withinPct }: { errorPct: number | null; withinPct: number }) {
  return (
    <span className={`text-xs rounded px-1.5 py-0.5 font-medium ${TONE_CLASS[errorTone(errorPct, withinPct)]}`}>
      {formatError(errorPct)}
    </span>
  );
}

const TH = "px-4 py-2 font-medium";
const TH_R = "px-4 py-2 text-right font-medium";

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-lg border border-subtle bg-surface-1">
      <header className="flex flex-wrap items-baseline gap-2 px-4 py-3">
        <h3 className="text-14 font-semibold text-primary">{title}</h3>
        {note && <span className="text-xs text-tertiary">{note}</span>}
      </header>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

export function TasksTable({
  workspaceSlug,
  tasks,
  total,
  truncated,
  withinPct,
}: {
  workspaceSlug: string;
  tasks: TAccuracyTask[];
  total: number;
  truncated: boolean;
  withinPct: number;
}) {
  return (
    <Panel
      title="Tasks"
      note={truncated ? `Newest ${tasks.length} of ${total}` : `${total} completed ${total === 1 ? "task" : "tasks"}`}
    >
      {tasks.length === 0 ? (
        <div className="text-sm px-4 py-8 text-center text-tertiary">Nothing to show.</div>
      ) : (
        <table className="text-sm w-full" data-testid="accuracy-tasks">
          <thead className="bg-surface-2 text-left text-tertiary">
            <tr>
              <th className={TH}>Task</th>
              <th className={TH}>Estimated by</th>
              <th className={TH_R}>Estimate</th>
              <th className={TH_R}>Actual</th>
              <th className={TH_R}>Of estimate</th>
              <th className={TH_R}>Error</th>
              <th className={TH_R}>Estimator's share</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.issue_id} className="border-t border-subtle" data-testid="accuracy-task-row">
                <td className="px-4 py-2">
                  {t.is_deleted ? (
                    <span className="text-tertiary">{t.name} (deleted)</span>
                  ) : (
                    <Link
                      href={generateWorkItemLink({
                        workspaceSlug,
                        projectId: t.project_id,
                        issueId: t.issue_id,
                        projectIdentifier: t.project_identifier ?? undefined,
                        sequenceId: t.sequence_id ?? undefined,
                      })}
                      className="hover:underline"
                    >
                      {t.project_identifier && t.sequence_id ? `${t.project_identifier}-${t.sequence_id} ` : ""}
                      {t.name}
                    </Link>
                  )}
                  {t.grandfathered && (
                    <span
                      className="text-xs ml-2 rounded bg-layer-1 px-1.5 py-0.5 text-tertiary"
                      title="Estimate was added after work started"
                    >
                      added late
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-tertiary">{t.estimator?.display_name ?? "Unknown"}</td>
                <td className="px-4 py-2 text-right">
                  {formatMinutes(t.original_minutes)}
                  {t.reestimated && (
                    <span
                      className="text-xs ml-1 text-tertiary"
                      title={`Re-estimated to ${formatMinutes(t.current_minutes)}`}
                    >
                      (now {formatMinutes(t.current_minutes)})
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">{formatMinutes(t.actual_minutes)}</td>
                <td className="px-4 py-2 text-right">{formatRatio(t.ratio)}</td>
                <td className="px-4 py-2 text-right">
                  <ErrorBadge errorPct={t.error_pct} withinPct={withinPct} />
                </td>
                <td className="px-4 py-2 text-right text-tertiary">{formatShare(t.estimator_share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

type TGroupRow = TAccuracyStats & { key: string; label: string };

function GroupTable({
  title,
  note,
  rows,
  labelHeader,
  withinPct,
  testId,
}: {
  title: string;
  note?: string;
  rows: TGroupRow[];
  labelHeader: string;
  withinPct: number;
  testId: string;
}) {
  return (
    <Panel title={title} note={note}>
      {rows.length === 0 ? (
        <div className="text-sm px-4 py-8 text-center text-tertiary">Nothing to show.</div>
      ) : (
        <table className="text-sm w-full" data-testid={testId}>
          <thead className="bg-surface-2 text-left text-tertiary">
            <tr>
              <th className={TH}>{labelHeader}</th>
              <th className={TH_R}>Tasks</th>
              <th className={TH_R}>Estimate</th>
              <th className={TH_R}>Actual</th>
              <th className={TH_R}>Error</th>
              <th className={TH_R}>Typical miss</th>
              <th className={TH_R}>Within band</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.key}
                className={`border-t border-subtle ${r.low_sample ? "text-tertiary" : ""}`}
                data-low-sample={r.low_sample ? "true" : "false"}
              >
                <td className="px-4 py-2">{r.label}</td>
                <td className="px-4 py-2 text-right">
                  {r.n}
                  {r.low_sample && (
                    <span className="text-xs ml-1" title="Too few tasks to read">
                      (few)
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">{formatMinutes(r.original_minutes)}</td>
                <td className="px-4 py-2 text-right">{formatMinutes(r.actual_minutes)}</td>
                <td className="px-4 py-2 text-right">
                  {r.low_sample ? (
                    formatError(r.error_pct)
                  ) : (
                    <ErrorBadge errorPct={r.error_pct} withinPct={withinPct} />
                  )}
                </td>
                <td className="px-4 py-2 text-right">{formatAbsError(r.median_abs_error_pct)}</td>
                <td className="px-4 py-2 text-right">{formatShare(r.within_share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

export function ProjectsTable({
  projects,
  truncated,
  withinPct,
}: {
  projects: TAccuracyProject[];
  truncated: boolean;
  withinPct: number;
}) {
  const rows = projects.map((p) => ({
    ...p,
    key: p.project_id,
    label: p.project_identifier ? `${p.project_identifier} · ${p.project_name ?? ""}` : (p.project_name ?? "Project"),
  }));
  return (
    <GroupTable
      title="Projects"
      note={truncated ? "Largest 500 shown" : undefined}
      rows={rows}
      labelHeader="Project"
      withinPct={withinPct}
      testId="accuracy-projects"
    />
  );
}

export function PeopleTable({
  people,
  truncated,
  withinPct,
}: {
  people: TAccuracyPerson[];
  truncated: boolean;
  withinPct: number;
}) {
  const rows = people.map((p) => ({
    ...p,
    key: p.user?.id ?? "unknown",
    label: p.user?.display_name ?? "Unknown estimator",
  }));
  return (
    <GroupTable
      title="People"
      note={truncated ? "Largest 500 shown" : "Credited to whoever set the original estimate"}
      rows={rows}
      labelHeader="Estimator"
      withinPct={withinPct}
      testId="accuracy-people"
    />
  );
}
