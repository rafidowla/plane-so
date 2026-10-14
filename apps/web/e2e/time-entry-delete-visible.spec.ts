/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-36: the delete (x) on a time-log entry must be visible AND
// reachable in the full-screen work item view, not clipped by the sidebar.
import { expect, test } from "@playwright/test";
import { createIssue, createWorklog, deleteIssue, getE2EProject, openWorkItem } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json", viewport: { width: 1280, height: 800 } });

test("the delete x is inside the visible sidebar in the full-screen view", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: worklog delete visible");
  await createWorklog(request, project.id, issue.id, 30);
  try {
    await openWorkItem(page, project.id, issue.id);
    const deleteButton = page.getByRole("button", { name: "Delete time entry" });
    await expect(deleteButton).toHaveCount(1);
    // toBeInViewport accounts for ancestors that clip overflow.
    await expect(deleteButton).toBeInViewport({ ratio: 1 });
  } finally {
    await deleteIssue(request, project.id, issue.id);
  }
});
