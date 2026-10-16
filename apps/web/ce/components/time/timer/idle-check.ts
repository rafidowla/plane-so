/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — the "Still working?" state machine, kept free of React/DOM so the
// due -> chiming -> confirmed | expired -> resume flow is unit-testable. All times are compared on
// the SERVER clock (browser clock + offset), never the raw browser clock.
import type { TRunningTimer, TTimerIssueRef } from "../../issues/worklog/types";

/**
 * Poll cadence for the running timer. The server picks a random check time and never announces it
 * ahead of time, so while the check is on we poll every 15 s to learn that it is due.
 */
export const TIMER_POLL_MS = 30_000;
export const TIMER_POLL_CHECK_MS = 15_000;
/** If the timer vanished within this much of the deadline, assume the server's sweep stopped it. */
export const EXPIRY_SLACK_MS = 15_000;

export type TIdlePhase = "off" | "running" | "due" | "expired";

const toMs = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
};

/** The server's "check is due" block: null until the (random, unannounced) check time has passed. */
function dueCheck(timer: TRunningTimer | undefined) {
  const check = timer?.check;
  if (!timer?.id || !timer.idle_check_enabled || !check) return null;
  const promptedAt = toMs(check.prompted_at);
  const deadlineAt = toMs(check.deadline_at);
  if (promptedAt === null || deadlineAt === null) return null;
  return { promptedAt, deadlineAt, promptedAtIso: check.prompted_at, deadlineAtIso: check.deadline_at };
}

/** Where the running timer is in its "Still working?" cycle. `serverNowMs` is on the server clock. */
export function getIdlePhase(timer: TRunningTimer | undefined, serverNowMs: number): TIdlePhase {
  if (!timer?.id || !timer.idle_check_enabled) return "off";
  const check = dueCheck(timer);
  if (!check) return "running";
  return serverNowMs < check.deadlineAt ? "due" : "expired";
}

/** 10 h reminder: the server flagged it, or its reminder time has passed. */
export function isOverReminder(timer: TRunningTimer | undefined, serverNowMs: number): boolean {
  if (!timer?.id) return false;
  if (timer.reminded) return true;
  const at = toMs(timer.remind_at);
  return at !== null && serverNowMs >= at;
}

/** Polling interval (ms) for the running-timer query: 15 s while the check is on, else 30 s. */
export function getTimerPollInterval(timer: TRunningTimer | undefined): number {
  return timer?.id && timer.idle_check_enabled ? TIMER_POLL_CHECK_MS : TIMER_POLL_MS;
}

/** The work item to resume after an expired check. */
export type TIdleItem = { issueId: string; projectId: string | null; ref: TTimerIssueRef | null };

export type TIdleState =
  | { kind: "none"; answered: string | null }
  | { kind: "prompt"; checkAt: string; deadlineAt: string; item: TIdleItem; answered: string | null }
  | { kind: "stopped"; item: TIdleItem; answered: string | null };

export const initialIdleState: TIdleState = { kind: "none", answered: null };

export type TIdleEvent =
  /** A fresh timer reading (or its absence) plus the current server-clock time. */
  | { type: "tick"; timer: TRunningTimer | undefined; serverNowMs: number }
  /** "I'm still here" succeeded here or in another tab for this check. */
  | { type: "answered"; checkAt: string }
  /** The user stopped the timer (here or in another tab), or started a different one. */
  | { type: "cleared" }
  /** The server said a late "I'm still here" came too late: the timer is already stopped. */
  | { type: "expired" };

function itemOf(timer: TRunningTimer): TIdleItem | null {
  if (!timer.issue) return null;
  return { issueId: timer.issue, projectId: timer.project ?? null, ref: timer.issue_detail ?? null };
}

export function reduceIdle(state: TIdleState, event: TIdleEvent): TIdleState {
  switch (event.type) {
    case "answered":
      // Remember which check was answered so a stale poll cannot re-open the prompt.
      return { kind: "none", answered: event.checkAt };
    case "cleared":
      return { kind: "none", answered: state.answered };
    case "expired":
      return state.kind === "prompt" ? { kind: "stopped", item: state.item, answered: state.answered } : state;
    case "tick": {
      const { timer, serverNowMs } = event;
      if (timer?.id) {
        const phase = getIdlePhase(timer, serverNowMs);
        const item = itemOf(timer);
        const check = dueCheck(timer);
        if (phase === "due" && item && check) {
          if (check.promptedAtIso === state.answered) return state;
          if (state.kind === "prompt" && state.checkAt === check.promptedAtIso) return state;
          return {
            kind: "prompt",
            checkAt: check.promptedAtIso,
            deadlineAt: check.deadlineAtIso,
            item,
            answered: state.answered,
          };
        }
        if (phase === "expired" && item) {
          // Deadline passed but the server has not swept yet: already as good as stopped.
          if (state.kind === "stopped") return state;
          return { kind: "stopped", item, answered: state.answered };
        }
        // running / off: a prompt that is no longer due was answered elsewhere; a "stopped" notice is
        // superseded by a timer that is running again.
        if (state.kind === "prompt" || state.kind === "stopped") return { kind: "none", answered: state.answered };
        return state;
      }
      // No timer.
      if (state.kind === "prompt") {
        const deadline = toMs(state.deadlineAt);
        const swept = deadline !== null && serverNowMs >= deadline - EXPIRY_SLACK_MS;
        return swept
          ? { kind: "stopped", item: state.item, answered: state.answered }
          : { kind: "none", answered: state.answered };
      }
      return state;
    }
    default:
      return state;
  }
}

/** Milliseconds from now (server clock) until the prompt window closes; 0 when already closed. */
export function getPromptRemainingMs(state: TIdleState, serverNowMs: number): number {
  if (state.kind !== "prompt") return 0;
  const deadline = toMs(state.deadlineAt);
  return deadline === null ? 0 : Math.max(0, deadline - serverNowMs);
}
