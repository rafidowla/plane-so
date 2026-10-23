/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import {
  checkWindow,
  describeRequestError,
  formatWindow,
  isInsideSession,
  isoToZonedDate,
  isoToZonedInput,
  safeTimeZone,
  shouldRefreshAfter,
  zonedInputToIso,
} from "./request-utils";

describe("time zones", () => {
  it("reads a typed time as wall-clock time in the workspace zone", () => {
    // Asia/Dhaka is UTC+6 all year, America/Toronto is UTC-4 in June and UTC-5 in January.
    expect(zonedInputToIso("2026-06-22T09:00", "Asia/Dhaka")).toBe("2026-06-22T03:00:00.000Z");
    expect(zonedInputToIso("2026-06-22T09:00", "America/Toronto")).toBe("2026-06-22T13:00:00.000Z");
    expect(zonedInputToIso("2026-01-15T09:00", "America/Toronto")).toBe("2026-01-15T14:00:00.000Z");
  });

  it("round-trips through the zone", () => {
    const iso = zonedInputToIso("2026-06-22T23:30", "Asia/Dhaka") as string;
    expect(isoToZonedInput(iso, "Asia/Dhaka")).toBe("2026-06-22T23:30");
  });

  it("puts an instant on the zone's calendar day, not UTC's", () => {
    // 20:00 UTC on the 22nd is already the 23rd in Dhaka.
    expect(isoToZonedDate("2026-06-22T20:00:00Z", "Asia/Dhaka")).toBe("2026-06-23");
    expect(isoToZonedDate("2026-06-22T20:00:00Z", "America/Toronto")).toBe("2026-06-22");
  });

  it("handles the spring-forward gap without throwing", () => {
    // 02:30 on 2026-03-08 does not exist in Toronto; any sane instant is acceptable, never null.
    expect(zonedInputToIso("2026-03-08T02:30", "America/Toronto")).not.toBeNull();
  });

  it("returns null for junk and falls back to UTC for an unknown zone", () => {
    expect(zonedInputToIso("not a date", "UTC")).toBeNull();
    expect(safeTimeZone("Mars/Olympus")).toBe("UTC");
    expect(safeTimeZone(undefined)).toBe("UTC");
    expect(zonedInputToIso("2026-06-22T09:00", "Mars/Olympus")).toBe("2026-06-22T09:00:00.000Z");
  });
});

describe("formatWindow", () => {
  it("shows start, end and length in the workspace zone", () => {
    const out = formatWindow("2026-06-22T03:00:00Z", "2026-06-22T04:30:00Z", "Asia/Dhaka");
    expect(out).toContain("09:00");
    expect(out).toContain("10:30");
    expect(out).toContain("1h 30m");
  });
  it("falls back to minutes when an entry has no interval", () => {
    expect(formatWindow(null, null, "UTC", 90)).toBe("1h 30m");
    expect(formatWindow(null, null, "UTC", null)).toBe("—");
  });
});

describe("checkWindow", () => {
  const now = new Date("2026-06-22T12:00:00Z").getTime();
  it("accepts a past interval and reports its minutes", () => {
    const r = checkWindow("2026-06-22T08:00", "2026-06-22T09:15", "UTC", now);
    expect(r).toMatchObject({ ok: true, minutes: 75, startIso: "2026-06-22T08:00:00.000Z" });
  });
  it("refuses end before start, the future, and more than 24 hours", () => {
    expect(checkWindow("2026-06-22T09:00", "2026-06-22T08:00", "UTC", now)).toMatchObject({ ok: false });
    expect(checkWindow("2026-06-22T09:00", "2026-06-22T13:00", "UTC", now)).toMatchObject({
      ok: false,
      message: "Time can't be logged in the future.",
    });
    expect(checkWindow("2026-06-20T09:00", "2026-06-21T10:00", "UTC", now)).toMatchObject({
      ok: false,
      message: "One entry can cover at most 24 hours.",
    });
    expect(checkWindow("", "2026-06-22T09:00", "UTC", now)).toMatchObject({ ok: false });
  });
});

describe("isInsideSession", () => {
  const start = "2026-06-22T08:00:00Z";
  const end = "2026-06-22T12:00:00Z";
  it("is strict at both ends", () => {
    expect(isInsideSession("2026-06-22T10:00:00Z", start, end)).toBe(true);
    expect(isInsideSession(start, start, end)).toBe(false);
    expect(isInsideSession(end, start, end)).toBe(false);
    expect(isInsideSession(null, start, end)).toBe(false);
  });
});

describe("describeRequestError", () => {
  it("lists the entries an overlap clashes with, in workspace time", () => {
    const info = describeRequestError(
      {
        code: "overlap",
        overlaps_timer: true,
        overlapping: [
          {
            id: "1",
            issue_ref: "PSR-12",
            issue_name: "Fix login",
            started_at: "2026-06-22T03:00:00Z",
            ended_at: "2026-06-22T04:00:00Z",
          },
        ],
      },
      "Asia/Dhaka"
    );
    expect(info.message).toContain("overlaps");
    expect(info.message).toContain("running timer");
    expect(info.details).toHaveLength(1);
    expect(info.details[0]).toContain("PSR-12 Fix login");
    expect(info.details[0]).toContain("09:00");
  });
  it("words the locked, pending and stale cases plainly", () => {
    expect(describeRequestError({ code: "entry_locked" }).message).toContain("locked");
    expect(describeRequestError({ code: "pending_exists" }).message).toContain("waiting for review");
    expect(describeRequestError({ code: "stale" }).message).toContain("Reject it");
  });
  it("keeps the server's sentence for invalid_interval and falls back for the unknown", () => {
    expect(describeRequestError({ code: "invalid_interval", error: "The end must be after the start." }).message).toBe(
      "The end must be after the start."
    );
    expect(describeRequestError(undefined).message).toBe("Something went wrong. Try again.");
  });
  it("flags the codes that mean the list is out of date", () => {
    expect(shouldRefreshAfter("not_pending")).toBe(true);
    expect(shouldRefreshAfter("stale")).toBe(true);
    expect(shouldRefreshAfter("note_required")).toBe(false);
    expect(shouldRefreshAfter(null)).toBe(false);
  });
});
