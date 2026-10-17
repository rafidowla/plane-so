/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 (S6): task estimates. A project that requires estimates refuses a state move
// until the item has one; setting it unblocks the move and locks the original; a later change is a
// re-estimate that shows in the history. Guests see no estimate controls.
// Sessions: admin (qa-visual, project admin), guest (qa-guest, project guest).
// Needs the seed's "In Progress" (started) state and the guest user: see e2e/README.md.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject, openWorkItem } from "./helpers";

const SHOT_DIR = process.env.E2E_SHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` });
};

async function setMode(
  request: APIRequestContext,
  projectId: string,
  mode: "off" | "optional" | "required",
  extra: Record<string, unknown> = {}
) {
  const res = await request.patch(`/api/workspaces/${E2E_WS}/projects/${projectId}/`, {
    data: { time_estimate_mode: mode, ...extra },
  });
  expect(res.ok(), `mode patch failed: ${res.status()} ${await res.text()}`).toBeTruthy();
}

test.describe("task estimates", () => {
  test("required mode blocks a state move until an estimate is set; re-estimate adds a revision", async ({
    browser,
  }) => {
    const admin = await browser.newContext({ storageState: "e2e/.auth/admin.json" });
    const page = await admin.newPage();
    const project = await getE2EProject(admin.request);
    let issueId = "";
    try {
      await setMode(admin.request, project.id, "required");
      const issue = await createIssue(admin.request, project.id, `E2E estimate ${Date.now()}`);
      issueId = issue.id;
      await openWorkItem(page, project.id, issue.id);

      const field = page.getByTestId("estimate-field");
      await expect(field).toBeVisible();
      await expect(page.getByTestId("estimate-required-note")).toBeVisible();

      // Move to the started state without an estimate: refused, with a clear message.
      await page.getByText("Todo", { exact: true }).first().click();
      await page.getByRole("option", { name: /In Progress/ }).click();
      await expect(page.getByText("Add an estimate first").first()).toBeVisible();
      // the estimate editor opened by itself
      await expect(page.getByTestId("estimate-input")).toBeVisible();
      await shot(page, "estimates-1-required-blocked");

      // Set 2h: saved, the required note goes away.
      await page.getByTestId("estimate-input").fill("2");
      await page.getByTestId("estimate-save").click();
      await expect(page.getByTestId("estimate-value")).toHaveText(/2h/);
      await expect(page.getByTestId("estimate-required-note")).toHaveCount(0);
      await expect(page.getByTestId("estimate-locked")).toHaveCount(0);

      // Replacing it before work starts is still allowed (original replaced), 3h now.
      await page.getByTestId("estimate-edit").click();
      await page.getByTestId("estimate-input").fill("3h");
      await page.getByTestId("estimate-save").click();
      await expect(page.getByTestId("estimate-value")).toHaveText(/3h/);

      // The move now works and locks the estimate.
      await page.getByText("Todo", { exact: true }).first().click();
      await page.getByRole("option", { name: /In Progress/ }).click();
      await expect(page.getByText("In Progress", { exact: true }).first()).toBeVisible();
      await expect(page.getByTestId("estimate-locked")).toBeVisible();
      await shot(page, "estimates-2-locked");

      // Re-estimate with a reason: the original stays on record.
      await page.getByTestId("estimate-edit").click();
      await expect(page.getByTestId("estimate-locked-note")).toBeVisible();
      await page.getByTestId("estimate-input").fill("5h");
      await page.getByTestId("estimate-reason").fill("scope grew");
      await page.getByTestId("estimate-save").click();
      await expect(page.getByTestId("estimate-value")).toHaveText(/5h/);
      await expect(page.getByTestId("estimate-original")).toContainText("3h");

      // History: oldest first.
      await page.getByTestId("estimate-history-button").click();
      const kinds = await page
        .getByTestId("estimate-revision")
        .evaluateAll((els) => els.map((e) => e.getAttribute("data-kind")));
      expect(kinds).toEqual(["original_set", "original_replaced", "re_estimate"]);
      await expect(page.getByTestId("estimate-history")).toContainText("scope grew");
      await shot(page, "estimates-3-history");
    } finally {
      if (issueId) await deleteIssue(admin.request, project.id, issueId);
      await setMode(admin.request, project.id, "off");
      await admin.close();
    }
  });

  test("guests see no estimate controls; off mode shows none either", async ({ browser }) => {
    const admin = await browser.newContext({ storageState: "e2e/.auth/admin.json" });
    const guest = await browser.newContext({ storageState: "e2e/.auth/guest.json" });
    const project = await getE2EProject(admin.request);
    let issueId = "";
    try {
      // guests only see their own work items unless the project lets them see all
      await setMode(admin.request, project.id, "optional", { guest_view_all_features: true });
      const issue = await createIssue(admin.request, project.id, `E2E estimate guest ${Date.now()}`);
      issueId = issue.id;

      const adminPage = await admin.newPage();
      await openWorkItem(adminPage, project.id, issue.id);
      await expect(adminPage.getByTestId("estimate-field")).toBeVisible();

      // a project member sees and can edit the field too
      const memberCtx = await browser.newContext({ storageState: "e2e/.auth/member.json" });
      const memberPage = await memberCtx.newPage();
      await openWorkItem(memberPage, project.id, issue.id);
      await expect(memberPage.getByTestId("estimate-field")).toBeVisible();
      await expect(memberPage.getByTestId("estimate-edit")).toBeVisible();
      await memberCtx.close();

      const guestPage = await guest.newPage();
      await guestPage.goto(`/${E2E_WS}/projects/${project.id}/issues/${issue.id}`);
      await expect(guestPage.getByText("Properties", { exact: true })).toBeVisible({
        timeout: 30_000,
      });
      await expect(guestPage.getByTestId("estimate-field")).toHaveCount(0);
      // the API agrees: no estimate card for a guest
      const res = await guest.request.get(
        `/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}/time-estimate/`
      );
      expect(res.status()).toBe(403);

      await setMode(admin.request, project.id, "off");
      await adminPage.reload();
      await expect(adminPage.getByText("Time tracking", { exact: true })).toBeVisible({ timeout: 30_000 });
      await expect(adminPage.getByTestId("estimate-field")).toHaveCount(0);
    } finally {
      if (issueId) await deleteIssue(admin.request, project.id, issueId);
      await setMode(admin.request, project.id, "off", { guest_view_all_features: false });
      await admin.close();
      await guest.close();
    }
  });
  test("project admin sets the estimate mode from project settings", async ({ browser }) => {
    const admin = await browser.newContext({ storageState: "e2e/.auth/admin.json" });
    const page = await admin.newPage();
    const project = await getE2EProject(admin.request);
    try {
      await setMode(admin.request, project.id, "off");
      await page.goto(`/${E2E_WS}/settings/projects/${project.id}/features/time-tracking/`);
      const setting = page.getByTestId("estimate-mode-setting");
      await expect(setting).toBeVisible({ timeout: 30_000 });
      await expect(setting).toHaveAttribute("data-mode", "off");

      await page.getByTestId("estimate-mode-select").click();
      await page.getByRole("option", { name: "Required" }).click();
      await expect(setting).toHaveAttribute("data-mode", "required");
      // the explanation says what "required" blocks
      await expect(page.getByTestId("estimate-mode-help")).toContainText("refused");
      await shot(page, "estimates-4-settings");

      // persisted on the server
      await page.reload();
      await expect(page.getByTestId("estimate-mode-setting")).toHaveAttribute("data-mode", "required", {
        timeout: 30_000,
      });
    } finally {
      await setMode(admin.request, project.id, "off");
      await admin.close();
    }
  });
  test("required mode refuses a timer start and a manual entry until an estimate exists", async ({ browser }) => {
    const admin = await browser.newContext({ storageState: "e2e/.auth/admin.json" });
    const page = await admin.newPage();
    const project = await getE2EProject(admin.request);
    let issueId = "";
    try {
      await setMode(admin.request, project.id, "required");
      const issue = await createIssue(admin.request, project.id, `E2E estimate timer ${Date.now()}`);
      issueId = issue.id;
      await openWorkItem(page, project.id, issue.id);
      await expect(page.getByTestId("estimate-field")).toBeVisible();

      // Start: refused, and the estimate editor opens.
      await page.getByRole("button", { name: "Start", exact: true }).click();
      await expect(page.getByText("Add an estimate first").first()).toBeVisible();
      await expect(page.getByTestId("estimate-input")).toBeVisible();
      await page.getByRole("button", { name: "Cancel" }).click();

      // Log time: refused too; the dialog closes and the estimate editor opens.
      await page.getByRole("button", { name: "Log time" }).click();
      const modal = page.getByRole("dialog").filter({ hasText: "Minutes" });
      await expect(modal).toBeVisible();
      await modal.locator('input[type="number"]').nth(1).fill("30");
      await modal.getByRole("button", { name: "Log time" }).click();
      await expect(modal).toHaveCount(0);
      await expect(page.getByText("Add an estimate first").first()).toBeVisible();
      await expect(page.getByTestId("estimate-input")).toBeVisible();

      // The API agrees: no entry was saved.
      const base = `/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}`;
      const manual = await admin.request.post(`${base}/worklogs/`, {
        data: { duration: 30, logged_date: new Date().toISOString().slice(0, 10), is_billable: false },
      });
      expect(manual.status()).toBe(400);
      expect(await manual.json()).toMatchObject({ code: "estimate_required" });
    } finally {
      if (issueId) {
        await admin.request.delete(`/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issueId}/worklog-timer/`);
        await deleteIssue(admin.request, project.id, issueId);
      }
      await setMode(admin.request, project.id, "off");
      await admin.close();
    }
  });
});
