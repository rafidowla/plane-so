/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-123 -- saving projects_layout must not wipe the other locally held display_filters keys
import { describe, expect, it } from "vitest";
import type { IWorkspaceUserPropertiesResponse } from "@plane/types";
import { mergeWorkspaceUserProperties } from "./workspace-user-properties";

const current = {
  display_filters: { group_by: "state", order_by: "-created_at", projects_layout: "list" },
  display_properties: { key: true },
  navigation_project_limit: 5,
} as unknown as IWorkspaceUserPropertiesResponse;

describe("mergeWorkspaceUserProperties", () => {
  it("merges display_filters so sibling keys survive", () => {
    const next = mergeWorkspaceUserProperties(current, { display_filters: { projects_layout: "grid" } });
    expect(next.display_filters).toEqual({ group_by: "state", order_by: "-created_at", projects_layout: "grid" });
    expect(next.display_properties).toEqual({ key: true });
    expect(next.navigation_project_limit).toBe(5);
  });

  it("does not mutate the previous value (rollback relies on it)", () => {
    mergeWorkspaceUserProperties(current, { display_filters: { projects_layout: "grid" } });
    expect(current.display_filters.projects_layout).toBe("list");
  });

  it("still replaces other top-level keys", () => {
    const next = mergeWorkspaceUserProperties(current, { navigation_project_limit: 0 });
    expect(next.navigation_project_limit).toBe(0);
    expect(next.display_filters).toEqual(current.display_filters);
  });

  it("handles missing current state", () => {
    const next = mergeWorkspaceUserProperties(undefined, { display_filters: { projects_layout: "grid" } });
    expect(next.display_filters).toEqual({ projects_layout: "grid" });
  });
});
