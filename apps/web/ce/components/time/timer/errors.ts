/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — friendly wording for the start/confirm/stop error payloads. The service
// rethrows the response body (the HTTP status is lost), so the machine-readable `code` is what we key on.
import type { TTimerApiError, TTimerErrorCode } from "../../issues/worklog/types";

const asPayload = (err: unknown): TTimerApiError =>
  typeof err === "object" && err !== null ? (err as TTimerApiError) : ({} as TTimerApiError);

const MESSAGES: Record<string, string> = {
  not_tracked: "Time tracking isn't turned on for you in this workspace. Ask a workspace admin.",
  tracking_disabled: "Time tracking is turned off for this workspace.",
  project_tracking_disabled: "Time tracking is turned off for this project.",
  issue_cancelled: "This work item is cancelled, so time can't be tracked on it.",
};

export type TTimerErrorInfo = {
  title: string;
  message: string;
  code: TTimerErrorCode | null;
  /** the item already has a running timer: not really an error, just resync */
  alreadyRunning: boolean;
};

export function describeTimerError(err: unknown, fallbackTitle = "Cannot start timer"): TTimerErrorInfo {
  const payload = asPayload(err);
  const code = (payload.code ?? null) as TTimerErrorCode | null;
  // A start on the item that is already running answers with the running timer and no code.
  if (!code && payload.timer) {
    return {
      title: "Already running",
      message: "A timer is already running on this work item.",
      code: null,
      alreadyRunning: true,
    };
  }
  const friendly = code ? MESSAGES[code] : undefined;
  return {
    title: fallbackTitle,
    message: friendly ?? payload.error ?? "Something went wrong. Try again.",
    code,
    alreadyRunning: false,
  };
}

/** True when a confirm/stop answer means "the server already stopped this timer". */
export function isStoppedByServer(err: unknown): boolean {
  const code = asPayload(err).code;
  return code === "idle_stopped" || code === "auto_stopped";
}
