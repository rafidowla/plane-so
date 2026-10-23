/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import type { TTimeRequest, TTimeRequestEvent } from "../../issues/worklog/types";
import { describeChange, eventLine, matchesType, reviewTitle } from "./request-utils";

const base = {
  old_started_at: "2026-06-22T13:00:00Z",
  old_ended_at: "2026-06-22T14:00:00Z",
  old_duration: 60,
  new_started_at: "2026-06-22T15:00:00Z",
  new_ended_at: "2026-06-22T16:30:00Z",
  new_duration: 90,
};
const request = (type: TTimeRequest["type"]) => ({ ...base, type }) as TTimeRequest;

describe("describeChange", () => {
  it("says what each kind of request does", () => {
    expect(describeChange(request("missing_time"), "UTC")).toMatch(/^Add /);
    expect(describeChange(request("time_change"), "UTC")).toMatch(/^Change .+ to .+/);
    expect(describeChange(request("deletion"), "UTC")).toMatch(/^Delete /);
    expect(describeChange(request("auto_stop_review"), "UTC")).toMatch(/stopped automatically/);
  });
});

describe("reviewTitle", () => {
  it("titles the dialog by mode and request kind", () => {
    expect(reviewTitle(request("missing_time"), "approve")).toBe("Approve request");
    expect(reviewTitle(request("auto_stop_review"), "approve")).toBe("Confirm auto-stopped time");
    expect(reviewTitle(request("deletion"), "reject")).toBe("Reject request");
    expect(reviewTitle(request("auto_stop_review"), "confirm_end")).toBe("Confirm with end time");
  });
});

describe("history events", () => {
  const event = {
    id: "e",
    request_id: "r",
    action: "approved",
    actor: { id: "u", display_name: "Pat" },
    actor_role: "workspace_admin",
    created_at: "2026-06-22T13:00:00Z",
    request: { type: "deletion" },
  } as unknown as TTimeRequestEvent;

  it("writes one line with who did it", () => {
    const line = eventLine(event, "UTC");
    expect(line).toContain("Pat");
    expect(line.split(" · ")).toHaveLength(3);
  });

  it("names the system when nobody acted", () => {
    expect(eventLine({ ...event, actor: null }, "UTC")).toContain("System");
  });

  it("filters events by request type; empty means all", () => {
    expect(matchesType(event, "")).toBe(true);
    expect(matchesType(event, "deletion")).toBe(true);
    expect(matchesType(event, "missing_time")).toBe(false);
  });
});
