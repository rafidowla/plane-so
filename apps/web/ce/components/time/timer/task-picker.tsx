/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — search-as-you-type picker over `time/my-tasks/`, with keyboard
// navigation (Up/Down move, Enter starts the timer, Esc closes the list).
import { useEffect, useId, useRef, useState } from "react";
import { Play, Search } from "lucide-react";
import useSWR from "swr";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TMyTask } from "../../issues/worklog/types";
import { TimerProjectTag } from "./project-tag";
import { startTimerOnItem } from "./timer-actions";

const DEBOUNCE_MS = 200;

/** Next highlighted index for an arrow key, wrapping at both ends. */
export function moveHighlight(current: number, delta: 1 | -1, count: number): number {
  if (count <= 0) return -1;
  if (current < 0) return delta === 1 ? 0 : count - 1;
  return (current + delta + count) % count;
}

type TProps = { workspaceSlug: string };

export function TaskPicker({ workspaceSlug }: TProps) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setQuery(text.trim()), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [text]);

  const { data, isLoading } = useSWR(
    `WORKSPACE_MY_TASKS_${workspaceSlug}_${query}`,
    () => timeTrackingService.getMyTasks(workspaceSlug, query, 15),
    { keepPreviousData: true }
  );
  const results: TMyTask[] = data?.results ?? [];

  useEffect(() => {
    setHighlight(results.length > 0 ? 0 : -1);
  }, [query, results.length]);

  const start = async (task: TMyTask) => {
    if (starting) return;
    setStarting(true);
    const started = await startTimerOnItem(workspaceSlug, task.id);
    setStarting(false);
    if (started) {
      setText("");
      setQuery("");
      setOpen(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => moveHighlight(h, 1, results.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => moveHighlight(h, -1, results.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const task = results[highlight];
      if (open && task) void start(task);
      else setOpen(true);
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  return (
    <div className="relative min-w-0 flex-1">
      <div className="flex h-9 items-center gap-2 rounded-md border border-subtle bg-surface-1 px-3 focus-within:border-accent-strong">
        <Search className="size-4 shrink-0 text-tertiary" aria-hidden />
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={highlight >= 0 ? `${listId}-${highlight}` : undefined}
          data-testid="time-task-picker-input"
          className="text-sm min-w-0 flex-1 bg-transparent outline-none placeholder:text-placeholder"
          placeholder="What are you working on? Search your work items to start a timer"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
        />
      </div>
      {open && (
        <ul
          id={listId}
          role="listbox"
          data-testid="time-task-picker-list"
          className="shadow-lg absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-subtle bg-surface-1 p-1"
        >
          {results.length === 0 ? (
            <li className="text-sm px-3 py-2 text-tertiary">
              {isLoading ? "Searching..." : query ? "No matching work items." : "No work items assigned to you."}
            </li>
          ) : (
            results.map((task, index) => (
              <li
                key={task.id}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === highlight}
                data-testid="time-task-option"
                // mousedown (not click) so it fires before the input's blur closes the list
                onMouseDown={(e) => {
                  e.preventDefault();
                  void start(task);
                }}
                onMouseEnter={() => setHighlight(index)}
                className={cn("text-sm flex cursor-pointer items-center gap-2 rounded px-3 py-1.5", {
                  "bg-layer-1-hover": index === highlight,
                })}
              >
                <span className="shrink-0 font-medium text-secondary">{task.ref}</span>
                <span className="min-w-0 flex-1 truncate text-primary">{task.name}</span>
                <TimerProjectTag projectId={task.project_id} fallbackLabel={task.project_identifier} />
                {task.state_name && <span className="text-xs shrink-0 text-tertiary">{task.state_name}</span>}
                {index === highlight && <Play className="size-3 shrink-0 text-tertiary" aria-hidden />}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
