/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — real UI login flow (no stored session): email step, password
// step, landing in the workspace.
import { expect, test } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL ?? "qa-visual@plane.test";
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "QaTest#2026";

test.use({ storageState: { cookies: [], origins: [] } });

test("sign in with email and password lands in the workspace", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Email" }).fill(E2E_EMAIL);
  await page.getByRole("button", { name: "Continue" }).click();

  const password = page.getByRole("textbox", { name: "Password" });
  await expect(password).toBeVisible();
  await password.fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Go to workspace" }).click();

  // Signed-in users leave the auth screens entirely.
  await expect(page).not.toHaveURL(/sign-in|onboarding/, { timeout: 30_000 });
});
