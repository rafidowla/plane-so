/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — Playwright suite for the QA regression flows (login, comments,
// time tracking, access control). See e2e/README.md for prerequisites.
import { defineConfig } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3002";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Workers are serial on purpose: several flows share one user-scoped timer,
  // and parallel issue sequences make assertions non-deterministic.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
  },
  outputDir: "./e2e/.artifacts",
  globalSetup: "./e2e/global-setup.ts",
});
