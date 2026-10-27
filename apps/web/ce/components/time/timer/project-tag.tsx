/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-129 — read-only project label next to a task on the timer bar and task picker. Filled in
// from the task's project; colour is the same one the Dashboard's range bar uses for that project.
import { observer } from "mobx-react";
import { cn } from "@plane/utils";
import { useProject } from "@/hooks/store/use-project";
import { getProjectColor } from "../dashboard/live-utils";

type TProps = { projectId: string; fallbackLabel?: string; className?: string };

export const TimerProjectTag = observer(function TimerProjectTag({ projectId, fallbackLabel, className }: TProps) {
  const { getPartialProjectById } = useProject();
  const project = getPartialProjectById(projectId);
  const label = project?.name ?? fallbackLabel;
  if (!label) return null;
  const color = getProjectColor({
    id: projectId,
    logo_props: (project?.logo_props ?? null) as Record<string, unknown> | null,
  });
  return (
    <span
      data-testid="time-timer-project"
      title={`Project: ${label}`}
      className={cn("text-xs flex max-w-48 shrink-0 items-center gap-1.5 text-secondary", className)}
    >
      <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
      <span className="truncate">{label}</span>
    </span>
  );
});
