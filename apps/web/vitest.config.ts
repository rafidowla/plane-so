/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: unit tests for fork-only frontend logic (apps/web has no other unit
// test setup). Playwright specs under e2e/ are NOT picked up here.
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // same aliases as tsconfig.json so component tests can import via "@/..."
  resolve: {
    alias: [
      { find: /^@\/plane-web\//, replacement: path.resolve(__dirname, "ce") + "/" },
      { find: /^@\//, replacement: path.resolve(__dirname) + "/" },
    ],
  },
  test: {
    include: ["ce/**/*.test.ts", "ce/**/*.test.tsx"],
    environment: "node",
  },
});
