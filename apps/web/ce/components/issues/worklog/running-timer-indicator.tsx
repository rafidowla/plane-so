/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-105 — small persistent indicator of the running time-tracking timer:
// work item reference (links to it), live elapsed time and a Stop button. Renders
// nothing when no timer runs. Two placements:
//  - "sidebar": the sidebar footer (replaces the former "Community" button)
//  - "topbar":  compact pill in the top navigation, shown when the sidebar is
//               collapsed or not rendered on the current page
import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { observer } from "mobx-react";
import { Square } from "lucide-react";
import { mutate as globalMutate } from "swr";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Icon } from "@makeplane/propel/components/icon";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import { setToast } from "@plane/blocks/toast";
import { cn, generateWorkItemLink } from "@plane/utils";
import { asApiError } from "@/plane-web/components/analytics/api-error";
import { revalidateRunningTimer, useRunningTimer, useTimerElapsed } from "@/plane-web/hooks/use-running-timer";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
// FORK: time-tracking-v2
import { isOverReminder } from "@/plane-web/components/time/timer/idle-check";
import { formatElapsed, formatLoggedMinutes } from "./timer-utils";
import { useIsTimerFooterMounted, useRegisterTimerFooter } from "./timer-footer-registry";

type TProps = { variant: "sidebar" | "topbar" };

const RunningTimerIndicator = observer(function RunningTimerIndicator({ variant }: TProps) {
  const { workspaceSlug } = useParams();
  const slug = workspaceSlug?.toString();
  const { timer, serverOffsetMs } = useRunningTimer(slug);
  const elapsed = useTimerElapsed(timer?.started_at, serverOffsetMs);
  const [isStopping, setIsStopping] = useState(false);

  if (!slug || !timer?.id) return null;

  const detail = timer.issue_detail;
  const label = detail?.ref ?? "Timer";
  const href = detail
    ? generateWorkItemLink({
        workspaceSlug: slug,
        projectId: detail.project_id,
        issueId: detail.id,
        projectIdentifier: detail.project_identifier,
        sequenceId: detail.sequence_id,
      })
    : undefined;

  const handleStop = async () => {
    if (!detail || isStopping) return;
    setIsStopping(true);
    try {
      const worklog = await timeTrackingService.stopTimer(slug, detail.project_id, detail.id);
      // Keep the work item's own widget in step if it is on screen.
      await Promise.all([
        revalidateRunningTimer(slug),
        globalMutate(`WORKLOG_TIMER_${slug}_${detail.id}`),
        globalMutate(`WORKLOGS_${slug}_${detail.id}`),
      ]);
      setToast({
        type: "success",
        title: `Stopped timer on ${detail.ref} (${formatLoggedMinutes(worklog?.duration ?? 0)} logged)`,
      });
    } catch (err) {
      setToast({ type: "error", title: "Cannot stop timer", message: asApiError(err).error ?? "Try again." });
      // The timer may already have been stopped elsewhere; resync.
      void revalidateRunningTimer(slug);
    } finally {
      setIsStopping(false);
    }
  };

  const time = (
    <span className="font-mono text-12 tabular-nums" aria-label="Elapsed time">
      {formatElapsed(elapsed)}
    </span>
  );
  // FORK: time-tracking-v2 — amber once the 10 h reminder state is reached (server flag or reminder time passed)
  const overLimit = isOverReminder(timer, Date.now() + serverOffsetMs);
  const dot = (
    <span
      aria-hidden
      data-state={overLimit ? "reminder" : "running"}
      className={cn("size-2 shrink-0 animate-pulse rounded-full", overLimit ? "bg-amber-500" : "bg-danger-primary")}
    />
  );

  return (
    <div
      role="status"
      aria-label={`Timer running on ${label}`}
      data-over-limit={overLimit ? "true" : undefined}
      data-testid={`running-timer-${variant}`}
      className={cn("flex min-w-0 items-center gap-2 text-secondary", {
        "w-full": variant === "sidebar",
        "h-7 rounded-md border border-subtle bg-layer-1 pr-1 pl-2": variant === "topbar",
      })}
    >
      {dot}
      <Tooltip label={detail ? `${detail.ref}: ${detail.name}` : "Open work item"} side="top">
        {href ? (
          <Link href={href} className="flex min-w-0 flex-1 items-center gap-2 hover:text-primary">
            <span className="truncate text-13 font-medium">{label}</span>
            {time}
          </Link>
        ) : (
          <span className="flex min-w-0 flex-1 items-center gap-2">
            <span className="truncate text-13 font-medium">{label}</span>
            {time}
          </span>
        )}
      </Tooltip>
      <IconButton
        size="sm"
        variant="ghost"
        icon={<Icon icon={Square} />}
        aria-label="Stop timer"
        onClick={handleStop}
        disabled={isStopping || !detail}
      />
    </div>
  );
});

/** Sidebar footer slot. Renders nothing (not even the footer bar) when no timer runs. */
export const RunningTimerSidebarFooter = observer(function RunningTimerSidebarFooter() {
  useRegisterTimerFooter();
  const { workspaceSlug } = useParams();
  const { timer } = useRunningTimer(workspaceSlug?.toString());
  if (!timer) return null;
  return (
    <div className="flex h-12 items-center border-t border-subtle bg-surface-1 p-3">
      <RunningTimerIndicator variant="sidebar" />
    </div>
  );
});

/** Top-bar slot: only when the sidebar footer indicator is not on screen. */
export const RunningTimerTopbar = observer(function RunningTimerTopbar({
  sidebarCollapsed,
}: {
  sidebarCollapsed: boolean;
}) {
  const isFooterMounted = useIsTimerFooterMounted();
  if (isFooterMounted && !sidebarCollapsed) return null;
  return <RunningTimerIndicator variant="topbar" />;
});
