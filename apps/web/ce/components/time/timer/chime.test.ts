/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import { CHIME_INTERVAL_MS, createChimeScheduler } from "./chime";

// A tiny fake clock + interval runner, so the 5 s x 5 min cadence is tested without real time.
function makeClock() {
  let t = 0;
  let handler: (() => void) | null = null;
  let every = 0;
  return {
    now: () => t,
    setIntervalFn: (fn: () => void, ms: number) => {
      handler = fn;
      every = ms;
      return 1;
    },
    clearIntervalFn: () => {
      handler = null;
    },
    advance(ms: number) {
      const end = t + ms;
      while (t + every <= end) {
        const run = handler;
        if (!run) break;
        t += every;
        run();
      }
      t = end;
    },
    active: () => handler !== null,
  };
}

describe("createChimeScheduler", () => {
  it("chimes at once, then every 5 s for 5 minutes, then stops", () => {
    const clock = makeClock();
    let plays = 0;
    const s = createChimeScheduler({
      play: () => {
        plays += 1;
        return true;
      },
      durationMs: 5 * 60_000,
      ...clock,
    });
    expect(CHIME_INTERVAL_MS).toBe(5000);
    s.start();
    expect(plays).toBe(1);
    clock.advance(5 * 60_000);
    // t = 0, 5, 10, ... 295 s => 60 chimes; the tick at 300 s sees the window closed.
    expect(plays).toBe(60);
    expect(s.isRunning()).toBe(false);
    clock.advance(60_000);
    expect(plays).toBe(60);
  });

  it("stops the moment it is told to (prompt answered)", () => {
    const clock = makeClock();
    let plays = 0;
    const s = createChimeScheduler({
      play: () => {
        plays += 1;
        return true;
      },
      durationMs: 5 * 60_000,
      ...clock,
    });
    s.start();
    clock.advance(12_000);
    expect(plays).toBe(3);
    s.stop();
    clock.advance(60_000);
    expect(plays).toBe(3);
    expect(clock.active()).toBe(false);
  });

  it("reports a blocked player once and keeps trying", () => {
    const clock = makeClock();
    let blocked = 0;
    let plays = 0;
    const s = createChimeScheduler({
      play: () => {
        plays += 1;
        return false;
      },
      onBlocked: () => {
        blocked += 1;
      },
      durationMs: 60_000,
      ...clock,
    });
    s.start();
    clock.advance(20_000);
    expect(blocked).toBe(1);
    expect(plays).toBe(5);
  });

  it("a throwing player counts as blocked and does not break the schedule", () => {
    const clock = makeClock();
    let blocked = 0;
    const s = createChimeScheduler({
      play: () => {
        throw new Error("no audio");
      },
      onBlocked: () => {
        blocked += 1;
      },
      durationMs: 60_000,
      ...clock,
    });
    s.start();
    clock.advance(10_000);
    expect(blocked).toBe(1);
    expect(s.isRunning()).toBe(true);
  });

  it("start is idempotent while running", () => {
    const clock = makeClock();
    let plays = 0;
    const s = createChimeScheduler({
      play: () => {
        plays += 1;
        return true;
      },
      durationMs: 60_000,
      ...clock,
    });
    s.start();
    s.start();
    expect(plays).toBe(1);
  });
});
