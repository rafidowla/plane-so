/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — pick the work item a missing-time request is for (search over `time/my-tasks/`).
import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import useSWR from "swr";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TMyTask } from "../../issues/worklog/types";

export type TPickedIssue = { id: string; ref: string; name: string };

type TProps = {
  workspaceSlug: string;
  value: TPickedIssue | null;
  onChange: (issue: TPickedIssue | null) => void;
  disabled?: boolean;
};

const DEBOUNCE_MS = 200;

export function IssuePicker({ workspaceSlug, value, onChange, disabled }: TProps) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setQuery(text.trim()), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [text]);

  const { data, isLoading } = useSWR(
    open ? `WORKSPACE_MY_TASKS_${workspaceSlug}_${query}` : null,
    () => timeTrackingService.getMyTasks(workspaceSlug, query, 15),
    { keepPreviousData: true }
  );
  const results: TMyTask[] = data?.results ?? [];

  if (value) {
    return (
      <div
        data-testid="request-issue-selected"
        className="text-sm flex h-9 items-center gap-2 rounded-md border border-subtle bg-surface-1 px-3"
      >
        <span className="shrink-0 font-medium text-secondary">{value.ref}</span>
        <span className="min-w-0 flex-1 truncate text-primary">{value.name}</span>
        {!disabled && (
          <button
            type="button"
            aria-label="Choose a different work item"
            className="shrink-0 text-tertiary hover:text-primary"
            onClick={() => onChange(null)}
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="relative">
      <div className="flex h-9 items-center gap-2 rounded-md border border-subtle bg-surface-1 px-3 focus-within:border-accent-strong">
        <Search className="size-4 shrink-0 text-tertiary" aria-hidden />
        <input
          data-testid="request-issue-input"
          className="text-sm min-w-0 flex-1 bg-transparent outline-none placeholder:text-placeholder"
          placeholder="Search your work items"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
        />
      </div>
      {open && (
        <ul
          role="listbox"
          data-testid="request-issue-list"
          className="shadow-lg absolute z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-subtle bg-surface-1 p-1"
        >
          {results.length === 0 ? (
            <li className="text-sm px-3 py-2 text-tertiary">
              {isLoading ? "Searching..." : query ? "No matching work items." : "No work items assigned to you."}
            </li>
          ) : (
            results.map((task) => (
              <li
                key={task.id}
                role="option"
                aria-selected={false}
                data-testid="request-issue-option"
                onMouseDown={(e) => {
                  e.preventDefault();
                  onChange({ id: task.id, ref: task.ref, name: task.name });
                  setText("");
                  setOpen(false);
                }}
                className={cn(
                  "text-sm flex cursor-pointer items-center gap-2 rounded px-3 py-1.5 hover:bg-layer-1-hover"
                )}
              >
                <span className="shrink-0 font-medium text-secondary">{task.ref}</span>
                <span className="min-w-0 flex-1 truncate text-primary">{task.name}</span>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
