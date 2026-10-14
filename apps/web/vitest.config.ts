/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: unit tests for pure fork logic under ce/ (the app's own vite.config.ts pulls in
// the React Router plugin, which unit tests do not need).
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["ce/**/*.test.ts"],
  },
});
