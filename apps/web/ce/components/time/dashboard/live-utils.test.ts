/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import type { TLiveRow } from "../../issues/worklog/types";
import {
  formatAgo,
  getLiveRowWork,
  getProjectColor,
  getRangeSegments,
  getRowElapsedSeconds,
  getRowTodayMinutes,
  PRIVATE_PROJECT_LABEL,
} from "./live-utils";

const T0 = Date.parse("2026-10-16T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

const baseRow = (over: Partial<TLiveRow> = {}): TLiveRow =>
  ({
    user: { id: "u1", display_name: "Ada", first_name: "Ada", last_name: "L", avatar_url: null },
    status: "running",
    today_minutes: 30,
    timer: {
      started_at: iso(T0 - 90_000),
      private: false,
      label: "",
      issue: { id: "i1", ref: "E2E-1", name: "Fix login" },
      project: { id: "p1", name: "E2E", identifier: "E2E" },
    },
    range: null,
    last_stopped_at: null,
    is_me: false,
    today_logged_minutes: 0,
    ...over,
  }) as unknown as TLiveRow;

describe("getLiveRowWork (redaction rendering)", () => {
  it("shows ref, name and project for a visible timer", () => {
    expect(getLiveRowWork(baseRow())).toEqual({
      kind: "item",
      ref: "E2E-1",
      name: "Fix login",
      projectName: "E2E",
      projectIdentifier: "E2E",
      issueId: "i1",
    });
  });

  it("a private row shows 'private project' and nothing else", () => {
    const row = baseRow({
      timer: { started_at: iso(T0), private: true, label: "private project", issue: null, project: null },
    } as unknown as Partial<TLiveRow>);
    expect(getLiveRowWork(row)).toEqual({ kind: "private", label: "private project" });
    expect(PRIVATE_PROJECT_LABEL).toBe("private project");
  });

  it("never leaks detail even if a private payload carried it", () => {
    const row = baseRow({
      timer: {
        started_at: iso(T0),
        private: true,
        label: "",
        issue: { id: "i9", ref: "SEC-9", name: "Secret" },
        project: { id: "p9", name: "Secret proj", identifier: "SEC" },
      },
    } as unknown as Partial<TLiveRow>);
    const work = getLiveRowWork(row);
    expect(work).toEqual({ kind: "private", label: "private project" });
    expect(JSON.stringify(work)).not.toContain("SEC-9");
  });

  it("an idle row has no work", () => {
    expect(getLiveRowWork(baseRow({ status: "stopped", timer: null } as unknown as Partial<TLiveRow>))).toEqual({
      kind: "idle",
    });
  });
});

describe("ticking", () => {
  it("elapsed uses the server offset", () => {
    const row = baseRow();
    // client clock 5 min behind the server
    expect(getRowElapsedSeconds(row, T0 - 5 * 60_000, 5 * 60_000)).toBe(90);
    expect(
      getRowElapsedSeconds(baseRow({ status: "stopped", timer: null } as unknown as Partial<TLiveRow>), T0, 0)
    ).toBe(0);
  });
  it("today's minutes grow only for running rows, from the fetch time", () => {
    const row = baseRow();
    expect(getRowTodayMinutes(row, T0 + 59_000, T0)).toBe(30);
    expect(getRowTodayMinutes(row, T0 + 3 * 60_000 + 1000, T0)).toBe(33);
    const idle = baseRow({ status: "stopped", timer: null } as unknown as Partial<TLiveRow>);
    expect(getRowTodayMinutes(idle, T0 + 10 * 60_000, T0)).toBe(30);
  });
});

describe("formatAgo", () => {
  it("formats relative times", () => {
    expect(formatAgo(null, T0, 0)).toBe("");
    expect(formatAgo(iso(T0 - 20_000), T0, 0)).toBe("just now");
    expect(formatAgo(iso(T0 - 5 * 60_000), T0, 0)).toBe("5m ago");
    expect(formatAgo(iso(T0 - 3 * 3600_000), T0, 0)).toBe("3h ago");
    expect(formatAgo(iso(T0 - 50 * 3600_000), T0, 0)).toBe("2d ago");
  });
});

describe("range bars", () => {
  it("returns segments proportional to minutes, plus the grey other segment", () => {
    const segments = getRangeSegments({
      total_minutes: 100,
      projects: [
        { id: "p1", name: "A", identifier: "A", minutes: 60, logo_props: null },
        { id: "p2", name: "B", identifier: "B", minutes: 30, logo_props: { icon: { color: "#ff0000" } } },
      ],
      other_projects: { minutes: 10, label: "other projects" },
    } as unknown as NonNullable<TLiveRow["range"]>);
    expect(segments.map((s) => [s.key, Math.round(s.percent)])).toEqual([
      ["p1", 60],
      ["p2", 30],
      ["other", 10],
    ]);
    expect(segments[1].color).toBe("#ff0000");
  });
  it("is empty for an empty range", () => {
    expect(
      getRangeSegments({
        total_minutes: 0,
        projects: [],
        other_projects: { minutes: 0, label: "x" },
      } as unknown as NonNullable<TLiveRow["range"]>)
    ).toEqual([]);
  });
  it("project colour is stable per id", () => {
    expect(getProjectColor({ id: "abc", logo_props: null })).toBe(getProjectColor({ id: "abc", logo_props: null }));
  });
});
