/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-57: comments taller than the collapse threshold render
// clamped with a Show more toggle; expanding and collapsing round-trips.
import { expect, test } from "@playwright/test";
import {
  LONG_COMMENT_HTML,
  createIssue,
  deleteIssue,
  getE2EProject,
  openWorkItem,
  postComment,
  stopTimer,
} from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("long comment collapses behind Show more and expands on click", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: long comment collapse");

  try {
    await postComment(request, project.id, issue.id, LONG_COMMENT_HTML);
    await openWorkItem(page, project.id, issue.id);

    const showMore = page.getByRole("button", { name: /show more/i });
    await expect(showMore).toBeVisible();

    // Clamped: the tail of the comment is clipped, the toggle is per comment.
    await showMore.click();
    await expect(page.getByRole("button", { name: /show less/i })).toBeVisible();

    await page.getByRole("button", { name: /show less/i }).click();
    await expect(page.getByRole("button", { name: /show more/i })).toBeVisible();
  } finally {
    await stopTimer(request, project.id, issue.id);
    await deleteIssue(request, project.id, issue.id);
  }
});
