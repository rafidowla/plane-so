/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — PSR-101 / PSR-105 (supersedes the PSR-26 conflict spec): starting a
// timer on a second work item stops the running one (and says so), and a persistent
// indicator shows the running timer everywhere until it is stopped.
import { expect, test } from "@playwright/test";
import {
  E2E_PROJECT_IDENTIFIER,
  E2E_WS,
  createIssue,
  deleteIssue,
  getE2EProject,
  openWorkItem,
  stopTimer,
} from "./helpers";

test.use({ storageState: "e2e/.auth/admin.json" });

test("starting a timer elsewhere stops the running one; the indicator tracks it", async ({ page, request }) => {
  const project = await getE2EProject(request);
  const issueA = await createIssue(request, project.id, "E2E: timer auto-stop A");
  const issueB = await createIssue(request, project.id, "E2E: timer auto-stop B");
  const refA = `${E2E_PROJECT_IDENTIFIER}-${issueA.sequence_id}`;
  const refB = `${E2E_PROJECT_IDENTIFIER}-${issueB.sequence_id}`;

  try {
    // Start the timer on A via the UI.
    await openWorkItem(page, project.id, issueA.id);
    await page.getByRole("button", { name: "Start", exact: true }).click();
    await expect(page.getByRole("button", { name: /^Stop \d/ })).toBeVisible();

    // PSR-105: the indicator names A, and persists across a reload.
    const indicator = page.getByTestId("running-timer-sidebar");
    await expect(indicator).toContainText(refA);
    await page.reload();
    await expect(page.getByTestId("running-timer-sidebar")).toContainText(refA);

    // Start on B: no error — A is stopped and logged, and a toast says so.
    await openWorkItem(page, project.id, issueB.id);
    await page.getByRole("button", { name: "Start", exact: true }).click();
    await expect(page.getByText(new RegExp(`Stopped timer on ${refA} \\(\\d+m logged\\)`))).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("button", { name: /^Stop \d/ })).toBeVisible();
    await expect(page.getByTestId("running-timer-sidebar")).toContainText(refB);

    // A now carries the logged entry and no running timer.
    await openWorkItem(page, project.id, issueA.id);
    await expect(page.getByRole("button", { name: "Start", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete time entry" })).toHaveCount(1);

    // The indicator links to B and its Stop button ends the timer.
    await expect(page.getByTestId("running-timer-sidebar").getByRole("link")).toHaveAttribute(
      "href",
      new RegExp(`/${E2E_WS}/browse/${refB}/?$`)
    );
    await page.getByTestId("running-timer-sidebar").getByRole("button", { name: "Stop timer" }).click();
    await expect(page.getByTestId("running-timer-sidebar")).toHaveCount(0);
    await expect(page.getByTestId("running-timer-topbar")).toHaveCount(0);
  } finally {
    await stopTimer(request, project.id, issueA.id);
    await stopTimer(request, project.id, issueB.id);
    await deleteIssue(request, project.id, issueA.id);
    await deleteIssue(request, project.id, issueB.id);
  }
});

test("starting a timer on the work item that is already timed is still rejected", async ({ request }) => {
  const project = await getE2EProject(request);
  const issue = await createIssue(request, project.id, "E2E: timer same item");
  const url = `/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}/worklog-timer/`;
  try {
    expect((await request.post(url, { data: {} })).status()).toBe(201);
    const again = await request.post(url, { data: {} });
    expect(again.status()).toBe(400);
    expect((await again.json()).error).toContain(`${E2E_PROJECT_IDENTIFIER}-${issue.sequence_id}`);
  } finally {
    await stopTimer(request, project.id, issue.id);
    await deleteIssue(request, project.id, issue.id);
  }
});
