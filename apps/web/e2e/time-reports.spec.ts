/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 (S5): the Reports tab and the admin Settings tab. Sessions: admin
// (qa-visual) and member (qa-member). Screenshots land in E2E_SHOT_DIR when set.
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { E2E_WS, createIssue, createWorklog, deleteIssue, getE2EProject } from "./helpers";

const SHOT_DIR = process.env.E2E_SHOT_DIR;
const api = (path: string) => `/api/workspaces/${E2E_WS}/${path}`;

async function newSession(browser: Browser, who: "admin" | "member"): Promise<BrowserContext> {
  return browser.newContext({ storageState: `e2e/.auth/${who}.json` });
}

async function shot(page: Page, name: string) {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true });
}

async function meId(request: APIRequestContext): Promise<string> {
  return (await (await request.get("/api/users/me/")).json()).id;
}

const hours = (minutes: number) => `${(minutes / 60).toFixed(1)} h`;

async function report(request: APIRequestContext, query: string) {
  const res = await request.get(api(`time/report/?${query}`));
  return { status: res.status(), body: await res.json() };
}

/** The range the tab is showing, read from its date inputs (the default week follows the user's week start). */
async function shownRange(page: Page): Promise<{ from: string; to: string }> {
  return {
    from: await page.getByLabel("Start date").inputValue(),
    to: await page.getByLabel("End date").inputValue(),
  };
}

test("admin sees team tiles, a per-person switch and the breakdown tables", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const page = await admin.newPage();
  const project = await getE2EProject(admin.request);
  let issueId = "";
  try {
    const issue = await createIssue(admin.request, project.id, "E2E: report team tile work");
    issueId = issue.id;
    await createWorklog(admin.request, project.id, issue.id, 90);

    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("time-reports")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "Team report" })).toBeVisible();
    await Promise.all(
      ["logged", "expected", "expected-to-date", "balance", "weekend", "members"].map((id) =>
        expect(page.getByTestId(`tile-${id}`)).toBeVisible()
      )
    );
    await expect(page.getByTestId("report-person")).toBeVisible();
    await expect(page.getByTestId("report-members")).toBeVisible();
    await expect(page.getByTestId("report-charts")).toBeVisible();
    await expect(page.getByTestId("report-projects")).toBeVisible();
    await expect(page.getByTestId("report-tasks")).toContainText("E2E: report team tile work");

    // The Logged tile is the API's number for the same range.
    const { from, to } = await shownRange(page);
    const team = await report(admin.request, `from=${from}&to=${to}&user=team`);
    expect(team.status).toBe(200);
    await expect(page.getByTestId("tile-logged")).toHaveText(hours(team.body.tiles.logged_minutes));
    await shot(page, "reports-admin-team");

    // Picking a person swaps the member table for the weekly one.
    await page.getByTestId("report-person").selectOption("me");
    await expect(page.getByTestId("report-weeks")).toBeVisible();
    await expect(page.getByTestId("report-members")).toHaveCount(0);
    await shot(page, "reports-admin-person");
  } finally {
    if (issueId) await deleteIssue(admin.request, project.id, issueId);
    await admin.close();
  }
});

test("a member sees only their own report", async ({ browser }) => {
  const member = await newSession(browser, "member");
  const page = await member.newPage();
  try {
    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("time-reports")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "My report" })).toBeVisible();
    await expect(page.getByTestId("tile-logged")).toBeVisible();
    // No team switch, no people table, and no Settings tab.
    await expect(page.getByTestId("report-person")).toHaveCount(0);
    await expect(page.getByTestId("report-members")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Settings" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Reports" })).toBeVisible();
    await shot(page, "reports-member");

    // The server agrees: team scope and other people are refused with the documented codes.
    const { from, to } = await shownRange(page);
    const team = await report(member.request, `from=${from}&to=${to}&user=team`);
    expect(team.status).toBe(403);
    expect(team.body.code).toBe("forbidden_scope");
    const admin = await newSession(browser, "admin");
    try {
      const adminId = await meId(admin.request);
      const other = await report(member.request, `from=${from}&to=${to}&user=${adminId}`);
      expect(other.status).toBe(403);
      expect(other.body.code).toBe("forbidden_user");
    } finally {
      await admin.close();
    }

    // The Settings URL falls back to the first tab for a member.
    await page.goto(`/${E2E_WS}/time/settings/`);
    await expect(page.getByTestId("time-dashboard")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("time-settings")).toHaveCount(0);
  } finally {
    await member.close();
  }
});

test("the CSV button downloads the entry-level export", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const page = await admin.newPage();
  const project = await getE2EProject(admin.request);
  let issueId = "";
  try {
    const issue = await createIssue(admin.request, project.id, "E2E: csv export row");
    issueId = issue.id;
    await createWorklog(admin.request, project.id, issue.id, 30);
    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("tile-logged")).toBeVisible({ timeout: 30_000 });

    const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("report-csv").click()]);
    expect(download.suggestedFilename()).toMatch(/^time-report-\d{4}-\d{2}-\d{2}-to-\d{4}-\d{2}-\d{2}\.csv$/);
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const text = Buffer.concat(chunks).toString("utf8");
    expect(text.split("\n")[0]).toContain("Date,Member,Project,Task ID,Task title");
    expect(text).toContain("E2E: csv export row");
  } finally {
    if (issueId) await deleteIssue(admin.request, project.id, issueId);
    await admin.close();
  }
});

test("a capacity override set in Settings changes Expected in the report", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const member = await newSession(browser, "member");
  const page = await admin.newPage();
  const memberId = await meId(member.request);
  await page.goto(`/${E2E_WS}/time/reports/`);
  await expect(page.getByTestId("tile-logged")).toBeVisible({ timeout: 30_000 });
  const { from, to } = await shownRange(page);
  const before = await report(admin.request, `from=${from}&to=${to}&user=${memberId}`);
  expect(before.status).toBe(200);
  const defaultExpected: number = before.body.tiles.expected_minutes;
  // Pick a capacity that differs from whatever the work week gives.
  const capacityHours = defaultExpected === 10 * 60 ? 12 : 10;

  try {
    await page.goto(`/${E2E_WS}/time/settings/`);
    await expect(page.getByTestId("time-settings")).toBeVisible({ timeout: 30_000 });
    const row = page.locator(`[data-testid="settings-member-row"][data-user-id="${memberId}"]`);
    await expect(row).toBeVisible();
    await row.getByTestId("member-capacity-input").fill(String(capacityHours));
    await row.getByTestId("member-capacity-save").click();
    await expect(row.getByTestId("member-capacity-reset")).toBeVisible();
    await shot(page, "settings-capacity-set");

    const after = await report(admin.request, `from=${from}&to=${to}&user=${memberId}`);
    expect(after.body.tiles.expected_minutes).not.toBe(defaultExpected);

    // The Reports tab shows the new number for that person.
    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("report-person")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("report-person").selectOption(memberId);
    await expect(page.getByTestId("tile-expected")).toHaveText(hours(after.body.tiles.expected_minutes));
    await shot(page, "reports-capacity-applied");
  } finally {
    await admin.request.put(api(`time/members/${memberId}/capacity/`), { data: { weekly_minutes: null } });
    await admin.close();
    await member.close();
  }
});

test("Settings: rule edits are validated, then save", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const page = await admin.newPage();
  const original = await (await admin.request.get(api("time/settings/"))).json();
  try {
    await page.goto(`/${E2E_WS}/time/settings/`);
    await expect(page.getByTestId("settings-rules")).toBeVisible({ timeout: 30_000 });

    // Auto-stop at or before the reminder is refused before any request is sent.
    await page.getByTestId("setting-auto_stop_after_minutes").fill(String(original.remind_after_minutes));
    await expect(page.getByTestId("settings-save")).toBeDisabled();
    await expect(page.getByText("Auto-stop must come after the reminder.")).toBeVisible();

    // A valid edit saves and persists.
    const newAutoStop = original.auto_stop_after_minutes + 5;
    await page.getByTestId("setting-auto_stop_after_minutes").fill(String(newAutoStop));
    await expect(page.getByTestId("settings-save")).toBeEnabled();
    await page.getByTestId("settings-save").click();
    await expect(page.getByTestId("settings-save")).toBeDisabled();
    const saved = await (await admin.request.get(api("time/settings/"))).json();
    expect(saved.auto_stop_after_minutes).toBe(newAutoStop);
    await shot(page, "settings-rules");
  } finally {
    await admin.request.patch(api("time/settings/"), {
      data: {
        remind_after_minutes: original.remind_after_minutes,
        auto_stop_after_minutes: original.auto_stop_after_minutes,
      },
    });
    await admin.close();
  }
});

test("the old Analytics Time tab is gone and its link lands on Reports", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const page = await admin.newPage();
  try {
    await page.goto(`/${E2E_WS}/analytics/overview/`);
    await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("tab", { name: "Time", exact: true })).toHaveCount(0);
    await page.goto(`/${E2E_WS}/analytics/time/`);
    await expect(page).toHaveURL(new RegExp(`/${E2E_WS}/time/reports/?$`), { timeout: 30_000 });
    await expect(page.getByTestId("time-reports")).toBeVisible();
  } finally {
    await admin.close();
  }
});

test("the billing and weekly-approval switches decide what Reports and Analytics show", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const page = await admin.newPage();
  const original = await (await admin.request.get(api("time/settings/"))).json();
  const setSwitches = async (data: { billing_enabled: boolean; weekly_approval_enabled: boolean }) => {
    const res = await admin.request.patch(api("time/settings/"), { data });
    expect(res.ok(), `settings patch failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  };
  try {
    await setSwitches({ billing_enabled: false, weekly_approval_enabled: false });
    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("tile-logged")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("report-billing-tiles")).toHaveCount(0);
    await page.goto(`/${E2E_WS}/analytics/overview/`);
    await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("tab", { name: "Timesheets" })).toHaveCount(0);
    // A direct link to the hidden tab goes back to Overview.
    await page.goto(`/${E2E_WS}/analytics/timesheets/`);
    await expect(page).toHaveURL(new RegExp(`/${E2E_WS}/analytics/overview/?$`), { timeout: 30_000 });

    await setSwitches({ billing_enabled: true, weekly_approval_enabled: true });
    await page.goto(`/${E2E_WS}/time/reports/`);
    await expect(page.getByTestId("report-billing-tiles")).toBeVisible({ timeout: 30_000 });
    await shot(page, "reports-billing-on");
    await page.goto(`/${E2E_WS}/analytics/overview/`);
    await expect(page.getByRole("tab", { name: "Timesheets" })).toBeVisible({ timeout: 30_000 });
  } finally {
    await admin.request.patch(api("time/settings/"), {
      data: {
        billing_enabled: original.billing_enabled,
        weekly_approval_enabled: original.weekly_approval_enabled,
      },
    });
    await admin.close();
  }
});
