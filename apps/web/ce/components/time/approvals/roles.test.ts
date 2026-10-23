/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import { hasAdminProject } from "./roles";

describe("hasAdminProject", () => {
  it("is true only when the user administers at least one project", () => {
    expect(hasAdminProject({ a: 15, b: 20 })).toBe(true);
    expect(hasAdminProject({ a: 15, b: 5 })).toBe(false);
    expect(hasAdminProject({})).toBe(false);
    expect(hasAdminProject(undefined)).toBe(false);
  });
});
