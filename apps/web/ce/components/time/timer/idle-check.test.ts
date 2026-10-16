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
  TIMER_POLL_DUE_MS,
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
  idle_check_minutes: 30,
  idle_grace_seconds: 300,
  next_check_at: iso(T0 + 30 * MIN),
  check_deadline_at: iso(T0 + 35 * MIN),
  remind_at: iso(T0 + 600 * MIN),
  reminded: false,
  issue_detail: { id: "i1", name: "Fix", sequence_id: 1, project_id: "p1", project_identifier: "E2E", ref: "E2E-1" },
  ...over,
});

describe("getIdlePhase", () => {
  it("is off without a timer or with the check disabled", () => {
    expect(getIdlePhase(undefined, T0)).toBe("off");
    expect(getIdlePhase(makeTimer({ idle_check_minutes: 0, next_check_at: null, check_deadline_at: null }), T0)).toBe(
      "off"
    );
  });
  it("moves running -> due -> expired on the server clock", () => {
    const timer = makeTimer();
    expect(getIdlePhase(timer, T0 + 29 * MIN)).toBe("running");
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
  it("polls tightly around the prompt window only", () => {
    const timer = makeTimer();
    expect(getTimerPollInterval(timer, T0 + MIN)).toBe(TIMER_POLL_MS);
    expect(getTimerPollInterval(timer, T0 + 30 * MIN - 10_000)).toBe(TIMER_POLL_DUE_MS);
    expect(getTimerPollInterval(timer, T0 + 36 * MIN)).toBe(TIMER_POLL_MS);
    expect(getTimerPollInterval(undefined, T0)).toBe(TIMER_POLL_MS);
  });
});

describe("reduceIdle", () => {
  it("stays quiet while running, prompts when due", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 10 * MIN });
    expect(s.kind).toBe("none");
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 30 * MIN + 1000 });
    expect(s.kind).toBe("prompt");
    if (s.kind === "prompt") {
      expect(s.checkAt).toBe(timer.next_check_at);
      expect(s.item.issueId).toBe("i1");
    }
    // Repeated ticks do not create a new object (no re-chime).
    const again = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    expect(again).toBe(s);
  });

  it("confirmed: dismisses, and a stale poll cannot re-open the same check", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "answered", checkAt: timer.next_check_at as string });
    expect(s.kind).toBe("none");
    // Stale reading still showing the old next_check_at:
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 31 * MIN + 2000 });
    expect(s.kind).toBe("none");
    // Fresh reading with the moved schedule: running, still none.
    const moved = makeTimer({ next_check_at: iso(T0 + 61 * MIN), check_deadline_at: iso(T0 + 66 * MIN) });
    s = reduceIdle(s, { type: "tick", timer: moved, serverNowMs: T0 + 32 * MIN });
    expect(s.kind).toBe("none");
    // The next check prompts again.
    s = reduceIdle(s, { type: "tick", timer: moved, serverNowMs: T0 + 61 * MIN });
    expect(s.kind).toBe("prompt");
  });

  it("answered in another tab: the schedule moved, so the prompt closes", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    const moved = makeTimer({ next_check_at: iso(T0 + 61 * MIN), check_deadline_at: iso(T0 + 66 * MIN) });
    s = reduceIdle(s, { type: "tick", timer: moved, serverNowMs: T0 + 31 * MIN + 3000 });
    expect(s.kind).toBe("none");
  });

  it("expired: deadline passes with the timer still present -> stopped (Resume offered)", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "tick", timer, serverNowMs: T0 + 35 * MIN + 1000 });
    expect(s.kind).toBe("stopped");
    if (s.kind === "stopped") expect(s.item.ref?.ref).toBe("E2E-1");
  });

  it("expired: timer gone near the deadline -> stopped; gone early -> just cleared", () => {
    const timer = makeTimer();
    const prompt = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    const swept = reduceIdle(prompt, { type: "tick", timer: undefined, serverNowMs: T0 + 35 * MIN + 5000 });
    expect(swept.kind).toBe("stopped");
    const stoppedElsewhere = reduceIdle(prompt, { type: "tick", timer: undefined, serverNowMs: T0 + 32 * MIN });
    expect(stoppedElsewhere.kind).toBe("none");
  });

  it("a stopped notice is replaced once a timer runs again, and cleared on demand", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 36 * MIN });
    expect(s.kind).toBe("stopped");
    const fresh = makeTimer({ id: "t2", next_check_at: iso(T0 + 66 * MIN), check_deadline_at: iso(T0 + 71 * MIN) });
    const running = reduceIdle(s, { type: "tick", timer: fresh, serverNowMs: T0 + 37 * MIN });
    expect(running.kind).toBe("none");
    s = reduceIdle(s, { type: "cleared" });
    expect(s.kind).toBe("none");
  });

  it("a late Yes (server says already stopped) turns the prompt into the Resume state", () => {
    const timer = makeTimer();
    let s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    s = reduceIdle(s, { type: "expired" });
    expect(s.kind).toBe("stopped");
    expect(reduceIdle(initialIdleState, { type: "expired" }).kind).toBe("none");
  });

  it("never prompts when the check is off", () => {
    const off = makeTimer({ idle_check_minutes: 0, next_check_at: null, check_deadline_at: null });
    const s = reduceIdle(initialIdleState, { type: "tick", timer: off, serverNowMs: T0 + 900 * MIN });
    expect(s.kind).toBe("none");
  });
});

describe("getPromptRemainingMs", () => {
  it("counts down to the deadline", () => {
    const timer = makeTimer();
    const s = reduceIdle(initialIdleState, { type: "tick", timer, serverNowMs: T0 + 31 * MIN });
    expect(getPromptRemainingMs(s, T0 + 31 * MIN)).toBe(4 * MIN);
    expect(getPromptRemainingMs(s, T0 + 40 * MIN)).toBe(0);
    expect(getPromptRemainingMs(initialIdleState, T0)).toBe(0);
  });
});
