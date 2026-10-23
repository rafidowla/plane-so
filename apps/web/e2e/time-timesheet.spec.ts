/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 (S4): the My timesheet tab. Start a timer from the timesheet's own bar,
// stop it on the work item, and the one entry shows in both places; an admin can open a member's week
// (read-only); a member cannot see the person select.
import { expect, test, type APIRequestContext } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject, openWorkItem } from "./helpers";

const api = (path: string) => `/api/workspaces/${E2E_WS}/${path}`;

async function stopAnyTimer(request: APIRequestContext) {
  await request.post(api("time/timer/stop/"), { data: {} });
}

test.describe("My timesheet", () => {
  test.use({ storageState: "e2e/.auth/admin.json" });

  test("start in the timesheet bar, stop on the work item: one entry in both", async ({ page, request }) => {
    // Unique per run: deleting the item keeps its time ("(deleted)"), so a fixed name would match an
    // earlier run's entry on the same day.
    const name = `E2E: timesheet round trip ${Date.now()}`;
    const project = await getE2EProject(request);
    const issue = await createIssue(request, project.id, name);
    const me = await (await request.get("/api/users/me/")).json();
    await request.patch(`/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}/`, {
      data: { assignee_ids: [me.id] },
    });
    await stopAnyTimer(request);

    try {
      await page.goto(`/${E2E_WS}/time/timesheet/`);
      await expect(page.getByTestId("timesheet-tab")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("timesheet-title")).toHaveText("My timesheet");

      // 1. Start from the timesheet's timer bar, keyboard only.
      const input = page.getByTestId("time-task-picker-input");
      await expect(input).toBeVisible();
      await input.click();
      await input.fill(name);
      await expect(page.getByTestId("time-task-option").first()).toContainText(name);
      await input.press("Enter");
      await expect(page.getByTestId("time-timer-bar")).toContainText(name, {
        timeout: 15_000,
      });

      // 2. Stop it on the work item.
      await openWorkItem(page, project.id, issue.id);
      await page.getByRole("button", { name: /^Stop \d/ }).click();
      await expect(page.getByRole("button", { name: "Delete time entry" })).toHaveCount(1, { timeout: 15_000 });

      // 3. The timesheet shows the same single entry, without a manual refresh of the page's state.
      await page.goto(`/${E2E_WS}/time/timesheet/`);
      const group = page.getByTestId("timesheet-group").filter({ hasText: name });
      await expect(group).toHaveCount(1, { timeout: 30_000 });
      await expect(group.getByTestId("timesheet-entry")).toHaveCount(1);
      await expect(group.getByTestId("timesheet-group-badge")).toHaveCount(0); // a badge only for 2+ entries
      await expect(page.getByTestId("timesheet-issue-row").filter({ hasText: name })).toHaveCount(1);
      // One minute minimum: the grid's week total is not empty.
      await expect(page.getByTestId("timesheet-week-total")).not.toHaveText("–");
    } finally {
      await stopAnyTimer(request).catch(() => undefined);
      await deleteIssue(request, project.id, issue.id);
    }
  });

  test("an admin can open a member's week read-only", async ({ page }) => {
    await page.goto(`/${E2E_WS}/time/timesheet/`);
    const person = page.getByTestId("timesheet-person");
    await expect(person).toBeVisible({ timeout: 30_000 });
    await person.selectOption({ label: await person.locator("option").nth(1).innerText() });
    await expect(page.getByTestId("timesheet-readonly")).toBeVisible();
    await expect(page.getByTestId("timesheet-add-time")).toHaveCount(0);
    await expect(page.getByTestId("time-timer-bar")).toHaveCount(0);
    await page.getByRole("button", { name: "Back to my timesheet" }).click();
    await expect(page.getByTestId("timesheet-title")).toHaveText("My timesheet");
  });
});

test.describe("My timesheet as a member", () => {
  test.use({ storageState: "e2e/.auth/member.json" });

  test("members see only their own week and can ask for time on a past day", async ({ page }) => {
    await page.goto(`/${E2E_WS}/time/timesheet/`);
    await expect(page.getByTestId("timesheet-tab")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("timesheet-person")).toHaveCount(0);
    // My requests lives inside this tab now.
    await expect(page.getByTestId("my-requests")).toBeVisible();
    // The old standalone link lands here too.
    await page.goto(`/${E2E_WS}/time/requests/`);
    await expect(page.getByTestId("timesheet-tab")).toBeVisible({ timeout: 30_000 });

    // Last week: every tracked day offers a request.
    await page.getByTestId("timesheet-prev").click();
    const request = page.getByTestId("timesheet-request-day").first();
    await expect(request).toBeVisible();
    await request.click();
    await expect(page.getByTestId("request-modal")).toHaveAttribute("data-kind", "missing_time");
    await expect(page.getByTestId("request-start")).toHaveValue(/T09:00$/);
    await page.getByRole("button", { name: "Cancel" }).click();
  });
});
