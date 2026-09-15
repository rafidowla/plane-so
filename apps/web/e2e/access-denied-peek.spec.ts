/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-59, surface 3: the peek-overview side panel must show the
// access-denied screen when its issue fetch answers 403. The 403 is injected
// with a route interceptor (a non-member can't reach the list to click a row
// in the first place), which exercises exactly the wiring the fix added.
import { expect, test } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("peek overview shows access denied when the issue fetch answers 403", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: peek access denied");

  try {
    // The peek's detail fetch: /issues/{uuid}/?expand=... (note the slash
    // before the query — '*' does not cross '/' in Playwright globs).
    await page.route(`**/issues/${issue.id}/?*`, (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ error: "You don't have the required permissions." }),
      })
    );

    await page.goto(`/${E2E_WS}/projects/${project.id}/issues/`);
    const row = page.getByText("E2E: peek access denied", { exact: true });
    await expect(row).toBeVisible({ timeout: 30_000 });

    // Clicking a list row opens the side-panel peek view.
    await row.click();

    await expect(page.getByText("You don't have access to this work item")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/Ask a project admin to add you/i)).toBeVisible();
    await expect(page.getByText(/does not exist/i)).toBeHidden();
  } finally {
    await deleteIssue(request, project.id, issue.id);
  }
});
