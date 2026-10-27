/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — top of the Dashboard tab: the running timer (item, elapsed, Stop) or the
// task picker. Hidden entirely when the server says the caller isn't a tracked user.
import { useState } from "react";
import Link from "next/link";
import { Square } from "lucide-react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Icon } from "@makeplane/propel/components/icon";
import { cn, generateWorkItemLink } from "@plane/utils";
import { useRunningTimer, useTimerElapsed } from "@/plane-web/hooks/use-running-timer";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { formatElapsed } from "../../issues/worklog/timer-utils";
import { isOverReminder } from "./idle-check";
import { TimerProjectTag } from "./project-tag";
import { TaskPicker } from "./task-picker";
import { stopRunningTimer } from "./timer-actions";

export const TimerBar = observer(function TimerBar({ workspaceSlug }: { workspaceSlug: string }) {
  const { timer, serverOffsetMs } = useRunningTimer(workspaceSlug);
  const elapsed = useTimerElapsed(timer?.started_at, serverOffsetMs);
  const [stopping, setStopping] = useState(false);
  // The same key/query the picker uses with an empty search: gives us `tracked` without a second request.
  const { data } = useSWR(`WORKSPACE_MY_TASKS_${workspaceSlug}_`, () =>
    timeTrackingService.getMyTasks(workspaceSlug, "", 15)
  );

  // Not tracked (e.g. a guest): no timer UI at all. Unknown yet: wait rather than flash it.
  if (!data || data.tracked === false) return null;

  if (timer?.id) {
    const detail = timer.issue_detail;
    const amber = isOverReminder(timer, Date.now() + serverOffsetMs);
    const href = detail
      ? generateWorkItemLink({
          workspaceSlug,
          projectId: detail.project_id,
          issueId: detail.id,
          projectIdentifier: detail.project_identifier,
          sequenceId: detail.sequence_id,
        })
      : undefined;
    const title = (
      <>
        <span className="font-medium text-secondary">{detail?.ref ?? "Timer"}</span>
        {detail && <span className="min-w-0 truncate text-primary">{detail.name}</span>}
      </>
    );
    return (
      <div
        data-testid="time-timer-bar"
        className="mb-4 flex items-center gap-3 rounded-lg border border-subtle bg-surface-1 px-4 py-2"
      >
        <span
          aria-hidden
          className={cn("size-2.5 shrink-0 animate-pulse rounded-full", amber ? "bg-amber-500" : "bg-danger-primary")}
        />
        {href ? (
          <Link href={href} className="text-sm flex min-w-0 flex-1 items-center gap-2 hover:underline">
            {title}
          </Link>
        ) : (
          <div className="text-sm flex min-w-0 flex-1 items-center gap-2">{title}</div>
        )}
        {detail && <TimerProjectTag projectId={detail.project_id} fallbackLabel={detail.project_identifier} />}
        <span
          data-testid="time-timer-bar-elapsed"
          className={cn("font-mono text-14 tabular-nums", amber ? "text-amber-600" : "text-primary")}
        >
          {formatElapsed(elapsed)}
        </span>
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          icon={<Icon icon={Square} />}
          label="Stop"
          loading={stopping}
          data-testid="time-timer-bar-stop"
          onClick={async () => {
            setStopping(true);
            await stopRunningTimer(workspaceSlug, detail?.ref);
            setStopping(false);
          }}
        />
      </div>
    );
  }

  return (
    <div data-testid="time-timer-bar" className="mb-4 flex items-center gap-3">
      <TaskPicker workspaceSlug={workspaceSlug} />
    </div>
  );
});
