/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-127 — Time-area weeks are Monday to Sunday regardless of the profile "start of week".
import { describe, expect, it } from "vitest";
import { getPresetRange } from "./date-range-filter";

describe("getPresetRange weeks", () => {
  it("this_week runs Monday to Sunday on a mid-week day", () => {
    // Tue 27 Oct 2026
    expect(getPresetRange("this_week", new Date(2026, 9, 27, 12))).toEqual({
      startDate: "2026-10-26",
      endDate: "2026-11-01",
    });
  });

  it("a Sunday belongs to the week that started the Monday before", () => {
    // Sun 25 Oct 2026 — a Sunday-start week would put this in 25–31 Oct
    expect(getPresetRange("this_week", new Date(2026, 9, 25, 12))).toEqual({
      startDate: "2026-10-19",
      endDate: "2026-10-25",
    });
  });

  it("last_week is the previous Monday to Sunday", () => {
    expect(getPresetRange("last_week", new Date(2026, 9, 27, 12))).toEqual({
      startDate: "2026-10-19",
      endDate: "2026-10-25",
    });
  });
});
