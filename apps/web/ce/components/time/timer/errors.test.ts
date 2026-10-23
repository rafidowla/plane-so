/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import { describeTimerError, isStoppedByServer } from "./errors";

describe("describeTimerError", () => {
  it("recognises an already-running start by its code", () => {
    const info = describeTimerError({ error: "A timer is already running on X.", code: "already_running", timer: {} });
    expect(info.alreadyRunning).toBe(true);
    expect(info.title).toBe("Already running");
  });
  it("does not treat a bare timer field as already running", () => {
    expect(describeTimerError({ error: "nope", timer: {} }).alreadyRunning).toBe(false);
  });
  it("uses friendly wording for known codes", () => {
    expect(describeTimerError({ code: "not_tracked" }).message).toContain("Ask a workspace admin");
  });
});

describe("isStoppedByServer", () => {
  it("is true for idle_stopped and auto_stopped only", () => {
    expect(isStoppedByServer({ code: "idle_stopped" })).toBe(true);
    expect(isStoppedByServer({ code: "auto_stopped" })).toBe(true);
    expect(isStoppedByServer({ code: "not_tracked" })).toBe(false);
  });
});
