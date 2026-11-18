/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-123
import { describe, expect, it } from "vitest";
import { createLatestRequestTracker } from "./latest-request";

describe("createLatestRequestTracker", () => {
  it("only the latest call per key may roll back", () => {
    const tracker = createLatestRequestTracker();
    const first = tracker.begin("acme");
    const second = tracker.begin("acme");
    expect(tracker.isLatest("acme", first)).toBe(false);
    expect(tracker.isLatest("acme", second)).toBe(true);
  });

  it("a single call is latest, and keys are independent", () => {
    const tracker = createLatestRequestTracker();
    const a = tracker.begin("acme");
    tracker.begin("other");
    expect(tracker.isLatest("acme", a)).toBe(true);
  });
});
