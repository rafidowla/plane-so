/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking — PSR-101 / PSR-105 unit tests for the timer helpers.
import { describe, expect, it } from "vitest";
import {
  formatElapsed,
  formatLoggedMinutes,
  getAutoStopToastTitle,
  getElapsedSeconds,
  getServerOffsetMs,
} from "./timer-utils";
import type { TStoppedTimer } from "./types";

describe("formatLoggedMinutes", () => {
  it("formats minutes, hours and both", () => {
    expect(formatLoggedMinutes(25)).toBe("25m");
    expect(formatLoggedMinutes(120)).toBe("2h");
    expect(formatLoggedMinutes(90)).toBe("1h 30m");
    expect(formatLoggedMinutes(0)).toBe("0m");
  });
});

describe("formatElapsed", () => {
  it("shows mm:ss under an hour and h:mm:ss after", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(65)).toBe("01:05");
    expect(formatElapsed(3599)).toBe("59:59");
    expect(formatElapsed(3725)).toBe("1:02:05");
    expect(formatElapsed(36000)).toBe("10:00:00");
  });
  it("clamps negatives and floors fractions", () => {
    expect(formatElapsed(-5)).toBe("00:00");
    expect(formatElapsed(61.9)).toBe("01:01");
  });
});

describe("getServerOffsetMs / getElapsedSeconds", () => {
  const started = "2026-10-14T10:00:00.000Z";
  const startedMs = new Date(started).getTime();

  it("computes elapsed from the client clock when there is no offset", () => {
    expect(getElapsedSeconds(started, startedMs + 90_000)).toBe(90);
  });

  it("corrects a client clock that runs behind the server", () => {
    // client thinks it is 10:00:00 but the server says 10:05:00 -> offset +5 min
    const offset = getServerOffsetMs("2026-10-14T10:05:00.000Z", startedMs);
    expect(offset).toBe(300_000);
    expect(getElapsedSeconds(started, startedMs, offset)).toBe(300);
  });

  it("never returns a negative elapsed time", () => {
    expect(getElapsedSeconds(started, startedMs - 10_000)).toBe(0);
  });

  it("tolerates missing or invalid input", () => {
    expect(getElapsedSeconds(undefined, Date.now())).toBe(0);
    expect(getElapsedSeconds("not-a-date", Date.now())).toBe(0);
    expect(getServerOffsetMs(undefined, Date.now())).toBe(0);
    expect(getServerOffsetMs("garbage", Date.now())).toBe(0);
  });
});

describe("getAutoStopToastTitle", () => {
  const base = { worklog: {} } as unknown as TStoppedTimer;

  it("names the stopped work item and the logged time", () => {
    const stopped = {
      ...base,
      duration: 25,
      issue: { id: "i", name: "Fix it", sequence_id: 12, project_id: "p", project_identifier: "PSR", ref: "PSR-12" },
    } as TStoppedTimer;
    expect(getAutoStopToastTitle(stopped)).toBe("Stopped timer on PSR-12 (25m logged)");
  });

  it("falls back when the stopped work item no longer resolves", () => {
    expect(getAutoStopToastTitle({ ...base, duration: 90, issue: null })).toBe(
      "Stopped the previous timer (1h 30m logged)"
    );
  });

  it("returns null when nothing was stopped", () => {
    expect(getAutoStopToastTitle(undefined)).toBeNull();
  });
});
