/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-60: while a work item's custom-property values are still in
// flight, its chip holds a pulsing placeholder instead of blank space, then
// resolves to the real chip. The bulk values response is held with a route
// interceptor so the placeholder window is deterministic.
import { expect, test } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject, setOptionValue } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("Task Type chip shows a placeholder until values load, then the real chip", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: chip placeholder");
  await setOptionValue(request, project.id, issue.id, "task-type", "Bug");

  // Hold the bulk values read until released, so the mid-flight UI is
  // deterministic. NB: '*' does not cross '/' in Playwright globs.
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/work-item-property-values/**", async (route) => {
    await held;
    await route.continue();
  });

  try {
    await page.goto(`/${E2E_WS}/projects/${project.id}/issues/`);

    // Wait for the row itself first — once it is mounted and the values are
    // still held, the placeholder must be on screen (no timing race).
    await expect(page.getByText("E2E: chip placeholder", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    // The exact placeholder markup from card-chips.tsx: a small pulsing pill.
    await expect(page.locator("span.h-4.w-16.animate-pulse").first()).toBeVisible();
    // The real chip must NOT be there yet while values are in flight.
    await expect(page.getByText("Bug", { exact: true })).toHaveCount(0);

    // Once the held response lands, the placeholder is replaced by the chip.
    release();
    await expect(page.getByText("Bug", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  } finally {
    release();
    await deleteIssue(request, project.id, issue.id);
  }
});
