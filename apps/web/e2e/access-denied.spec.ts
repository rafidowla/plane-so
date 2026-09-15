/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-59: a workspace member who is NOT a project member gets an
// access-denied screen on a shared work-item link, not "does not exist".
// The outsider is a member of the e2e workspace (seed) but of no project.
import { expect, test } from "@playwright/test";
import { E2E_PROJECT_IDENTIFIER, E2E_WS, createIssue, deleteIssue, getE2EProject } from "./helpers";

test.use({ storageState: "e2e/.auth/outsider.json" });

test("shared work-item link denies a non-project member explicitly", async ({ page, playwright }) => {
  // Author the target item as the admin user so it definitely exists.
  const adminContext = await playwright.request.newContext({
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3002",
    storageState: "e2e/.auth/admin.json",
  });
  const project = await getE2EProject(adminContext);
  const issue = await createIssue(adminContext, project.id, "E2E: access denied target");

  try {
    await page.goto(`/${E2E_WS}/browse/${E2E_PROJECT_IDENTIFIER}-${issue.sequence_id}/`);

    await expect(page.getByText("You don't have access to this work item")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/not a member of this project/i)).toBeVisible();
    // The misleading copy must NOT be what the user sees.
    await expect(page.getByText(/does not exist/i)).toBeHidden();
  } finally {
    await deleteIssue(adminContext, project.id, issue.id);
    await adminContext.dispose();
  }
});
