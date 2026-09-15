/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-37: the X on a time-log entry must open a confirmation
// dialog; Cancel keeps the entry, Delete removes it.
import { expect, test } from "@playwright/test";
import { createIssue, createWorklog, deleteIssue, getE2EProject, openWorkItem, stopTimer } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("deleting a time entry asks for confirmation", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: worklog delete confirmation");
  await createWorklog(request, project.id, issue.id, 30);

  try {
    await openWorkItem(page, project.id, issue.id);

    const deleteButtons = page.getByRole("button", { name: "Delete time entry" });
    await expect(deleteButtons).toHaveCount(1);

    // 1. X opens the dialog; the entry is still listed behind it.
    await deleteButtons.click();
    const dialog = page.getByRole("dialog");
    // NB: headlessui's dialog root has no box; assert on its content.
    const dialogHeading = dialog.getByRole("heading", { name: "Delete time entry" });
    await expect(dialogHeading).toBeVisible();
    await expect(dialog.getByText(/Are you sure you want to delete this time entry/i)).toBeVisible();

    // 2. Cancel: dialog closes, entry survives.
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialogHeading).toBeHidden();
    await expect(deleteButtons).toHaveCount(1);

    // 3. Delete: entry is gone for good.
    await deleteButtons.click();
    await dialog.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(deleteButtons).toHaveCount(0);
  } finally {
    await stopTimer(request, project.id, issue.id);
    await deleteIssue(request, project.id, issue.id);
  }
});
