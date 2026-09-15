/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-26: starting a timer while another runs must fail with an
// error that NAMES the work item holding the running timer.
import { expect, test } from "@playwright/test";
import { E2E_PROJECT_IDENTIFIER, createIssue, deleteIssue, getE2EProject, openWorkItem, stopTimer } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("timer conflict error names the work item with the running timer", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issueA = await createIssue(request, project.id, "E2E: timer conflict A");
  const issueB = await createIssue(request, project.id, "E2E: timer conflict B");

  try {
    // Start the timer on A via the UI.
    await openWorkItem(page, project.id, issueA.id);
    await page.getByRole("button", { name: "Start", exact: true }).click();
    await expect(page.getByRole("button", { name: /^Stop/ })).toBeVisible();

    // Try to start a second timer on B.
    await openWorkItem(page, project.id, issueB.id);
    await page.getByRole("button", { name: "Start", exact: true }).click();

    // The toast must name A's reference (e.g. "E2E-12") and its title.
    const toast = page.getByText(/You already have a running timer on/);
    await expect(toast).toBeVisible({ timeout: 15_000 });
    await expect(toast).toContainText(`${E2E_PROJECT_IDENTIFIER}-${issueA.sequence_id}`);
    await expect(toast).toContainText("E2E: timer conflict A");
  } finally {
    await stopTimer(request, project.id, issueA.id);
    await stopTimer(request, project.id, issueB.id);
    await deleteIssue(request, project.id, issueA.id);
    await deleteIssue(request, project.id, issueB.id);
  }
});
