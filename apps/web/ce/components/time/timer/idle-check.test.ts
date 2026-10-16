/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import type { TRunningTimer } from "../../issues/worklog/types";
import {
  getIdlePhase,
  getPromptRemainingMs,
  getTimerPollInterval,
  initialIdleState,
  isOverReminder,
  reduceIdle,
  TIMER_POLL_CHECK_MS,
  TIMER_POLL_MS,
} from "./idle-check";

const T0 = Date.parse("2026-10-16T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const MIN = 60_000;

const makeTimer = (over: Partial<TRunningTimer> = {}): TRunningTimer => ({
  id: "t1",
  issue: "i1",
  project: "p1",
  started_at: iso(T0),
  idle_check_enabled: true,
  idle_grace_seconds: 300,
  check: null,
  remind_at: iso(T0 + 600 * MIN),
  reminded: false,
  issue_detail: { id: "i1", name: "Fix", sequence_id: 1, project_id: "p1", project_identifier: "E2E", ref: "E2E-1" },
  ...over,
});

// What the server returns once the (random, never pre-announced) check time has passed.
const checkAt = (min: number): TRunningTimer["check"] => ({
  prompted_at: iso(T0 + min * MIN),
  deadline_at: iso(T0 + (min + 5) * MIN),
});
// A timer whose check came due at `min` minutes (the poll that reveals it arrives later).
const dueTimer = (min = 30, over: Partial<TRunningTimer> = {}) => makeTimer({ check: checkAt(min), ...over });

describe("getIdlePhase", () => {
  it("is off without a timer or with the check disabled", () => {
    expect(getIdlePhase(undefined, T0)).toBe("off");
    expect(getIdlePhase(makeTimer({ idle_check_enabled: false }), T0)).toBe("off");
  });
  it("is running while the server reports no check, however long it has run", () => {
    expect(getIdlePhase(makeTimer(), T0 + 400 * MIN)).toBe("running");
  });
  it("moves due -> expired on the server clock once the server reveals the check", () => {
    const timer = dueTimer();
    expect(getIdlePhase(timer, T0 + 30 * MIN)).toBe("due");
    expect(getIdlePhase(timer, T0 + 34 * MIN + 59_000)).toBe("due");
    expect(getIdlePhase(timer, T0 + 35 * MIN)).toBe("expired");
  });
});

describe("isOverReminder", () => {
  it("is true once reminded or past remind_at", () => {
    expect(isOverReminder(makeTimer(), T0 + MIN)).toBe(false);
    expect(isOverReminder(makeTimer({ reminded: true }), T0 + MIN)).toBe(true);
    expect(isOverReminder(makeTimer(), T0 + 600 * MIN)).toBe(true);
    expect(isOverReminder(undefined, T0)).toBe(false);
  });
});

describe("getTimerPollInterval", () => {
  it("polls every 15 s while a timer runs with the check on, 30 s otherwise", () => {
    expect(getTimerPollInterval(makeTimer())).toBe(TIMER_POLL_CHECK_MS);
    expect(TIMER_POLL_CHECK_MS).toBe(15_000);
    expect(getTimerPollInterval(dueTimer())).toBe(TIMER_POLL_CHECK_MS);
    expect(getTimerPollInterval(makeTimer({ idle_check_enabled: false }))).toBe(TIMER_POLL_MS);
    expect(getTimerPollInterval(undefined)).toBe(TIMER_POLL_MS);
  });
});

describe("reduceIdle", () => {
  it("stays quiet while the server reports no check, prompts when it does", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer: makeTimer(), serverNowMs: T0 + 10 * MIN });
    expect(s.kind).toBe("none");
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 30 * MIN + 1000 });
    expect(s.kind).toBe("prompt");
    if (s.kind === "prompt") {
      expect(s.checkAt).toBe(timer.check?.prompted_at);
      expect(s.deadlineAt).toBe(timer.check?.deadline_at);
      expect(s.item.issueId).toBe("i1");
    }
    // Repeated ticks do not create a new object (no re-chime).
    const again = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    expect(again).toBe(s);
  });

  it("confirmed: dismisses, and a stale poll cannot re-open the same check", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "answered", checkAt: timer.check?.prompted_at as string });
    expect(s.kind).toBe("none");
    // Stale reading still showing the answered check:
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 31 * MIN + 2000 });
    expect(s.kind).toBe("none");
    // Fresh reading after the confirm: the new interval has no check yet, still none.
    s = reduceIdle(s, { type: "tick", timer: makeTimer(), serverNowMs: T0 + 32 * MIN });
    expect(s.kind).toBe("none");
    // The next (differently timed) check prompts again.
    s = reduceIdle(s, { type: "tick", timer: dueTimer(61), serverNowMs: T0 + 61 * MIN });
    expect(s.kind).toBe("prompt");
  });

  it("answered in another tab: the schedule moved, so the prompt closes", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "tick", timer: makeTimer(), serverNowMs: T0 + 31 * MIN + 3000 });
    expect(s.kind).toBe("none");
  });

  it("expired: deadline passes with the timer still present -> stopped (Start again offered)", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 35 * MIN + 1000 });
    expect(s.kind).toBe("stopped");
    if (s.kind === "stopped") expect(s.item.ref?.ref).toBe("E2E-1");
  });

  it("expired: timer gone near the deadline -> stopped; gone early -> just cleared", () => {
    const timer = dueTimer();
    const prompt = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    const swept = reduceIdle(prompt, { type: "tick", timer: undefined, serverNowMs: T0 + 35 * MIN + 5000 });
    expect(swept.kind).toBe("stopped");
    const stoppedElsewhere = reduceIdle(prompt, { type: "tick", timer: undefined, serverNowMs: T0 + 32 * MIN });
    expect(stoppedElsewhere.kind).toBe("none");
  });

  it("a stopped notice is replaced once a timer runs again, and cleared on demand", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 36 * MIN });
    expect(s.kind).toBe("stopped");
    const fresh = makeTimer({ id: "t2" });
    const running = reduceIdle(s, { type: "tick", timer: fresh, serverNowMs: T0 + 37 * MIN });
    expect(running.kind).toBe("none");
    s = reduceIdle(s, { type: "cleared" });
    expect(s.kind).toBe("none");
  });

  it("a late confirm (server says already stopped) turns the prompt into the Start again state", () => {
    const timer = dueTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "expired" });
    expect(s.kind).toBe("stopped");
    expect(reduceIdle(initialIdleState, { type: "expired" }).kind).toBe("none");
  });

  it("never prompts when the check is off", () => {
    const off = makeTimer({ idle_check_enabled: false });
    const s = reduceIdle(initialIdleState, { type: "tick", timer: off, serverNowMs: T0 + 900 * MIN });
    expect(s.kind).toBe("none");
  });
});

describe("getPromptRemainingMs", () => {
  it("counts down to the deadline", () => {
    const timer = dueTimer();
    const s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    expect(getPromptRemainingMs(s, T0 + 31 * MIN)).toBe(4 * MIN);
    expect(getPromptRemainingMs(s, T0 + 40 * MIN)).toBe(0);
    expect(getPromptRemainingMs(initialIdleState, T0)).toBe(0);
  });
});
