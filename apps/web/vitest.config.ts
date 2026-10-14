/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: unit tests for fork-only frontend logic (apps/web has no other unit
// test setup). Playwright specs under e2e/ are NOT picked up here.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["ce/**/*.test.ts", "ce/**/*.test.tsx"],
    environment: "node",
  },
});
