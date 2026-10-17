/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — Approvals filters: person, project, type and date range.
import type { TTimeRequest, TTimeRequestType } from "../../issues/worklog/types";
import { REQUEST_TYPE_LABEL } from "../requests/request-utils";

export type TApprovalFilters = {
  userId: string;
  projectId: string;
  type: "" | TTimeRequestType;
  /** YYYY-MM-DD, workspace days; empty = no bound */
  from: string;
  to: string;
};

export const EMPTY_FILTERS: TApprovalFilters = { userId: "", projectId: "", type: "", from: "", to: "" };

export const hasActiveFilters = (f: TApprovalFilters): boolean => Object.values(f).some(Boolean);

/** Keep requests whose work day falls inside [from, to] (both inclusive, either may be empty). */
export function withinDates<T extends { work_date: string }>(rows: T[], from: string, to: string): T[] {
  return rows.filter((r) => (!from || r.work_date >= from) && (!to || r.work_date <= to));
}

/** Distinct people and projects found in a set of requests, for the dropdowns. */
export function filterOptions(requests: TTimeRequest[]) {
  const people = new Map<string, string>();
  const projects = new Map<string, string>();
  for (const r of requests) {
    if (r.requester) people.set(r.requester.id, r.requester.display_name);
    if (r.project_id) projects.set(r.project_id, r.issue?.identifier ?? "");
  }
  // oxlint-disable-next-line unicorn/no-array-sort -- sorting a fresh copy; toSorted needs the es2023 lib
  const sortedPeople = [...people].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  return {
    people: sortedPeople,
    projects: [...projects].map(([id, identifier]) => ({ id, identifier })),
  };
}

const TYPES: TTimeRequestType[] = ["missing_time", "time_change", "deletion", "auto_stop_review"];

type TProps = {
  value: TApprovalFilters;
  onChange: (next: TApprovalFilters) => void;
  people: { id: string; name: string }[];
  projects: { id: string; label: string }[];
};

const selectClass = "text-sm rounded border border-subtle bg-transparent px-2 py-1.5";

export function ApprovalFilters({ value, onChange, people, projects }: TProps) {
  const set = (patch: Partial<TApprovalFilters>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="approval-filters">
      <select
        aria-label="Person"
        value={value.userId}
        onChange={(e) => set({ userId: e.target.value })}
        className={selectClass}
      >
        <option value="">Everyone</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <select
        aria-label="Project"
        value={value.projectId}
        onChange={(e) => set({ projectId: e.target.value })}
        className={selectClass}
      >
        <option value="">All projects</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
      <select
        aria-label="Type"
        value={value.type}
        onChange={(e) => set({ type: e.target.value as TApprovalFilters["type"] })}
        className={selectClass}
      >
        <option value="">All types</option>
        {TYPES.map((t) => (
          <option key={t} value={t}>
            {REQUEST_TYPE_LABEL[t]}
          </option>
        ))}
      </select>
      <input
        type="date"
        aria-label="From date"
        value={value.from}
        onChange={(e) => set({ from: e.target.value })}
        className={selectClass}
      />
      <span className="text-tertiary">→</span>
      <input
        type="date"
        aria-label="To date"
        value={value.to}
        onChange={(e) => set({ to: e.target.value })}
        className={selectClass}
      />
      {hasActiveFilters(value) && (
        <button
          type="button"
          className="text-sm text-accent-primary hover:underline"
          onClick={() => onChange(EMPTY_FILTERS)}
        >
          Clear
        </button>
      )}
    </div>
  );
}
