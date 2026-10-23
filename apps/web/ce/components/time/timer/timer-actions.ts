/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — start/stop through the workspace timer endpoints, with the toasts and
// cache refreshes every entry point (timer bar, resume, work item button) shares.
import { mutate as globalMutate } from "swr";
import { setToast } from "@plane/blocks/toast";
import { handleEstimateRequired } from "../../issues/worklog/estimate/estimate-required";
import { revalidateRunningTimer } from "@/plane-web/hooks/use-running-timer";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { formatLoggedMinutes } from "../../issues/worklog/timer-utils";
import type { TStateChange, TStoppedTimer, TTimerStartResponse } from "../../issues/worklog/types";
import { describeTimerError } from "./errors";
import { requestNotificationPermissionOnce } from "./notify";

/** SWR key prefix of the dashboard's live query, so every filter variant can be refreshed at once. */
export const liveKeyPrefix = (slug: string) => `TIME_LIVE_${slug}`;

/** SWR key prefix of the My timesheet week query (every week / person variant). */
export const timesheetKeyPrefix = (slug: string) => `TIME_TIMESHEET_${slug}`;

export const refreshTimeData = async (slug: string) => {
  await Promise.all([
    revalidateRunningTimer(slug),
    globalMutate((key) => typeof key === "string" && key.startsWith(liveKeyPrefix(slug))),
    globalMutate((key) => typeof key === "string" && key.startsWith(`WORKSPACE_MY_TASKS_${slug}`)),
    // FORK: time-tracking-v2 S4 — the My timesheet week follows every timer start/stop.
    globalMutate((key) => typeof key === "string" && key.startsWith(timesheetKeyPrefix(slug))),
  ]);
};

// FORK: PSR-121 — the server cuts a stopped run where the person's day reaches 24 h.
export const DAY_LIMIT_CLAMPED_MESSAGE = "Cut at 24 h for the day — that day already had other time on it.";

/** "Stopped E2E-12, logged 1h 20m" */
export const getStoppedToastTitle = (stopped: TStoppedTimer | null | undefined): string | null => {
  if (!stopped) return null;
  const logged = formatLoggedMinutes(stopped.duration);
  return stopped.issue
    ? `Stopped ${stopped.issue.ref}, logged ${logged}`
    : `Stopped the previous timer, logged ${logged}`;
};

/** "Moved to In Progress" */
export const getStateChangeToastTitle = (change: TStateChange | undefined): string | null =>
  change ? `Moved to ${change.to.name}` : null;

export const announceStartResult = (slug: string, started: TTimerStartResponse) => {
  const stoppedTitle = getStoppedToastTitle(started.stopped);
  if (stoppedTitle) {
    setToast({
      type: "info",
      title: stoppedTitle,
      message: started.stopped?.day_limit_clamped ? DAY_LIMIT_CLAMPED_MESSAGE : undefined,
    });
    const id = started.stopped?.issue?.id;
    if (id) {
      void globalMutate(`WORKLOG_TIMER_${slug}_${id}`);
      void globalMutate(`WORKLOGS_${slug}_${id}`);
    }
  }
  const moved = getStateChangeToastTitle(started.state_change);
  if (moved) setToast({ type: "success", title: moved });
};

/**
 * Start a timer on a work item. Call straight from the click handler: the notification permission
 * prompt must be triggered by the user gesture, so it is requested before the first await.
 * Returns the started timer, or null after showing a friendly message.
 */
export async function startTimerOnItem(slug: string, issueId: string): Promise<TTimerStartResponse | null> {
  requestNotificationPermissionOnce();
  try {
    const started = await timeTrackingService.startWorkspaceTimer(slug, issueId);
    announceStartResult(slug, started);
    globalMutate(`WORKLOG_TIMER_${slug}_${issueId}`);
    globalMutate(`WORKLOGS_${slug}_${issueId}`);
    await refreshTimeData(slug);
    return started;
  } catch (err) {
    // FORK: time-tracking-v2 S6 — no estimate on a project that needs one: the estimate toast replaces this one
    if (handleEstimateRequired(err, { issueId, workspaceSlug: slug })) {
      await refreshTimeData(slug);
      return null;
    }
    const info = describeTimerError(err);
    if (info.alreadyRunning) {
      setToast({ type: "info", title: info.title, message: info.message });
    } else {
      setToast({ type: "error", title: info.title, message: info.message });
    }
    await refreshTimeData(slug);
    return null;
  }
}

/** Stop the running timer (any item). Returns true when something was stopped. */
export async function stopRunningTimer(slug: string, ref?: string | null): Promise<boolean> {
  try {
    const stopped = await timeTrackingService.stopWorkspaceTimer(slug);
    const logged = formatLoggedMinutes(stopped?.total_duration ?? stopped?.duration ?? 0);
    setToast({
      type: "success",
      title: ref ? `Stopped ${ref}, logged ${logged}` : `Timer stopped, logged ${logged}`,
      message: stopped?.day_limit_clamped ? DAY_LIMIT_CLAMPED_MESSAGE : undefined,
    });
    if (stopped?.issue) {
      globalMutate(`WORKLOG_TIMER_${slug}_${stopped.issue}`);
      globalMutate(`WORKLOGS_${slug}_${stopped.issue}`);
    }
    await refreshTimeData(slug);
    return true;
  } catch (err) {
    const info = describeTimerError(err, "Cannot stop timer");
    setToast({ type: "error", title: info.title, message: info.message });
    await refreshTimeData(slug);
    return false;
  }
}
