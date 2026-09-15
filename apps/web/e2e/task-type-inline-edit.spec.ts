/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-34: clicking the Task Type chip on a list-view row opens
// the option picker and changing it updates the work item inline.
import { expect, test } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject, setOptionValue } from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("Task Type chip in the list view opens a picker and changes the value", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: inline chip edit");
  await setOptionValue(request, project.id, issue.id, "task-type", "Bug");

  try {
    await page.goto(`/${E2E_WS}/projects/${project.id}/issues/`);
    const row = page.getByText("E2E: inline chip edit", { exact: true });
    await expect(row).toBeVisible({ timeout: 30_000 });

    // The chip itself is the dropdown trigger now.
    const chip = page.getByText("Bug", { exact: true }).first();
    await chip.click();

    // Picker opens with the property's options; pick Chore (menu items are
    // plain divs inside the shared Dropdown, not buttons).
    await page.getByText("Chore", { exact: true }).last().click();

    // The row's chip flips to Chore (and the API agrees).
    await expect(page.getByText("Chore", { exact: true }).first()).toBeVisible({ timeout: 10_000 });
    const values = await request.get(
      `/api/workspaces/${E2E_WS}/projects/${project.id}/work-item-property-values/?work_item_ids=${issue.id}`
    );
    const body = (await values.json()) as Record<string, Record<string, string[]>>;
    const propertyValues = Object.values(body[issue.id] ?? {})[0] ?? [];
    expect(propertyValues.length, "expected exactly one selected option").toBe(1);
  } finally {
    await deleteIssue(request, project.id, issue.id);
  }
});
