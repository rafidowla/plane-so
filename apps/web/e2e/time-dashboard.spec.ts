/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 (S2b): the live Time dashboard, the task picker, and the
// "Still working?" idle check. Uses four sessions: admin (qa-visual), member (qa-member), and
// qa-other, who is the only member of the private SEC project.
//
// The idle test is real-time (about 7 minutes: 1 min check + 5 min grace + the minute sweep), so it
// runs by default but can be skipped with E2E_SKIP_SLOW=1. Screenshots land in E2E_SHOT_DIR when set.
import { expect, test, type APIRequestContext, type Browser, type BrowserContext } from "@playwright/test";
import { E2E_PROJECT_IDENTIFIER, E2E_WS, createIssue, deleteIssue, getE2EProject } from "./helpers";

const SHOT_DIR = process.env.E2E_SHOT_DIR;

const api = (path: string) => `/api/workspaces/${E2E_WS}/${path}`;

async function newSession(browser: Browser, who: "admin" | "member" | "other"): Promise<BrowserContext> {
  return browser.newContext({ storageState: `e2e/.auth/${who}.json` });
}

async function stopAnyTimer(request: APIRequestContext) {
  await request.post(api("time/timer/stop/"), { data: {} });
}

async function startTimer(request: APIRequestContext, issueId: string) {
  const res = await request.post(api("time/timer/start/"), { data: { issue_id: issueId } });
  expect(res.ok(), `start failed: ${res.status()} ${await res.text()}`).toBeTruthy();
}

async function shot(page: import("@playwright/test").Page, name: string) {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` });
}

async function getSecProjectId(request: APIRequestContext): Promise<string> {
  const res = await request.get(api("projects/"));
  const body = await res.json();
  const list = (Array.isArray(body) ? body : body.results) as { id: string; identifier: string }[];
  const sec = list.find((p) => p.identifier === "SEC");
  if (!sec) throw new Error("SEC project missing — run scripts/e2e-seed.sh");
  return sec.id;
}

test("live dashboard: running rows appear within 5 s, private work stays private, picker starts a timer", async ({
  browser,
}) => {
  const admin = await newSession(browser, "admin");
  const member = await newSession(browser, "member");
  const other = await newSession(browser, "other");
  const adminPage = await admin.newPage();
  const memberPage = await member.newPage();
  let publicIssueId = "";
  let secIssueId = "";
  let secProjectId = "";
  let pickerIssueId = "";
  const project = await getE2EProject(admin.request);

  try {
    const publicIssue = await createIssue(admin.request, project.id, "E2E: dashboard visible work");
    publicIssueId = publicIssue.id;
    const publicRef = `${E2E_PROJECT_IDENTIFIER}-${publicIssue.sequence_id}`;
    const pickerIssue = await createIssue(admin.request, project.id, "E2E: dashboard picker target");
    pickerIssueId = pickerIssue.id;
    // Assign so it shows up under "my tasks".
    const me = await (await admin.request.get("/api/users/me/")).json();
    await admin.request.patch(`/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${pickerIssue.id}/`, {
      data: { assignee_ids: [me.id] },
    });
    secProjectId = await getSecProjectId(other.request);
    const secIssue = await createIssue(other.request, secProjectId, "E2E: top secret work");
    secIssueId = secIssue.id;

    await Promise.all([admin, member, other].map((c) => stopAnyTimer(c.request)));

    // Admin and member both watch the dashboard.
    await adminPage.goto(`/${E2E_WS}/time/dashboard/`);
    await memberPage.goto(`/${E2E_WS}/time/dashboard/`);
    await expect(adminPage.getByTestId("time-dashboard")).toBeVisible({ timeout: 30_000 });
    await expect(memberPage.getByTestId("time-dashboard")).toBeVisible({ timeout: 30_000 });

    // 1. The member starts a timer: the row shows up in the admin's dashboard within the 5 s poll.
    await startTimer(member.request, publicIssue.id);
    const memberRow = adminPage.getByTestId("live-row").filter({ hasText: publicRef });
    await expect(memberRow).toBeVisible({ timeout: 12_000 });
    await expect(memberRow).toHaveAttribute("data-status", "running");
    await expect(memberRow).toContainText("E2E: dashboard visible work");

    // 2. qa-other tracks in a project nobody else is in: both viewers see only "private project".
    await startTimer(other.request, secIssueId);
    const privateRow = adminPage.getByTestId("live-row-private").first();
    await expect(privateRow).toBeVisible({ timeout: 12_000 });
    await expect(privateRow).toHaveText(/private project/);
    await expect(memberPage.getByTestId("live-row-private").first()).toBeVisible({ timeout: 12_000 });
    await Promise.all(
      [adminPage, memberPage].flatMap((page) => [
        expect(page.getByTestId("live-rows")).not.toContainText("top secret"),
        expect(page.getByTestId("live-rows")).not.toContainText("SEC-"),
      ])
    );
    // Running first, with a ticking elapsed time.
    await expect(adminPage.getByTestId("live-row").first()).toHaveAttribute("data-status", "running");
    const elapsed = adminPage.getByTestId("live-row-elapsed").first();
    const before = await elapsed.innerText();
    await adminPage.waitForTimeout(2200);
    expect(await elapsed.innerText()).not.toBe(before);
    await shot(adminPage, "dashboard-admin");
    await shot(memberPage, "dashboard-member-private-row");

    // 3. The admin starts a timer from the picker with the keyboard only.
    const input = adminPage.getByTestId("time-task-picker-input");
    await expect(input).toBeVisible();
    await input.click();
    await input.fill("picker target");
    await expect(adminPage.getByTestId("time-task-option").first()).toContainText("E2E: dashboard picker target");
    await input.press("ArrowDown");
    await input.press("ArrowUp");
    await input.press("Enter");
    await expect(adminPage.getByTestId("time-timer-bar-stop")).toBeVisible({ timeout: 15_000 });
    await expect(adminPage.getByTestId("time-timer-bar")).toContainText("E2E: dashboard picker target");
    // ...and the member sees the admin's row appear.
    await expect(memberPage.getByTestId("live-row").filter({ hasText: "dashboard picker target" })).toBeVisible({
      timeout: 12_000,
    });

    // 4. Stop from the bar.
    await adminPage.getByTestId("time-timer-bar-stop").click();
    await expect(adminPage.getByTestId("time-task-picker-input")).toBeVisible({ timeout: 15_000 });
  } finally {
    await Promise.all([admin, member, other].map((c) => stopAnyTimer(c.request).catch(() => undefined)));
    if (publicIssueId) await deleteIssue(admin.request, project.id, publicIssueId);
    if (pickerIssueId) await deleteIssue(admin.request, project.id, pickerIssueId);
    if (secIssueId) await deleteIssue(other.request, secProjectId, secIssueId);
    await Promise.all([admin.close(), member.close(), other.close()]);
  }
});

test("idle check: prompt at the check time, I'm still here dismisses it everywhere, silence stops the timer and offers Start again", async ({
  browser,
}) => {
  test.skip(process.env.E2E_SKIP_SLOW === "1", "real-time idle test (about 16 min) skipped via E2E_SKIP_SLOW");
  test.setTimeout(30 * 60_000);

  const admin = await newSession(browser, "admin");
  const project = await getE2EProject(admin.request);
  const issue = await createIssue(admin.request, project.id, "E2E: idle check target");
  const ref = `${E2E_PROJECT_IDENTIFIER}-${issue.sequence_id}`;
  let previous: { min: number; max: number } | null = null;

  try {
    await stopAnyTimer(admin.request);
    // The schedule is based on max(start, last confirm, settings save): change the setting BEFORE starting.
    const current = await (await admin.request.get(api("time/settings/"))).json();
    previous = { min: current.idle_check_min_minutes, max: current.idle_check_max_minutes };
    // min = max makes the otherwise random (20-80 min) check time predictable. 5 is the smallest
    // window the settings API accepts (the check is either off, or 5-480 minutes).
    const patch = await admin.request.patch(api("time/settings/"), {
      data: { idle_check_min_minutes: 5, idle_check_max_minutes: 5 },
    });
    expect(patch.ok(), `settings patch failed: ${patch.status()}`).toBeTruthy();
    await startTimer(admin.request, issue.id);

    // The check time is never revealed before it is due.
    const early = await (await admin.request.get(api("time/timer/"))).json();
    expect(early.check).toBeNull();
    expect(early.idle_check_enabled).toBe(true);
    expect(early).not.toHaveProperty("next_check_at");

    // Two tabs of the same user.
    const tabA = await admin.newPage();
    const tabB = await admin.newPage();
    await tabA.goto(`/${E2E_WS}/time/dashboard/`);
    await tabB.goto(`/${E2E_WS}/time/dashboard/`);
    await expect(tabA.getByTestId("time-dashboard")).toBeVisible({ timeout: 30_000 });
    // A click in each tab lets the browser allow sound later (autoplay policy).
    await tabA.getByTestId("time-dashboard").click({ position: { x: 5, y: 5 } });
    await tabB.getByTestId("time-dashboard").click({ position: { x: 5, y: 5 } });

    // 1. The prompt appears in both tabs when the check is due (about 5 minutes after start, plus up to
    //    15 s for the poll).
    const dialogA = tabA.getByTestId("idle-check-dialog");
    const dialogB = tabB.getByTestId("idle-check-dialog");
    await expect(dialogA).toBeVisible({ timeout: 6 * 60_000 });
    await expect(dialogB).toBeVisible({ timeout: 15_000 });
    await expect(dialogA).toContainText("Still working?");
    await expect(tabA.getByTestId("idle-check-yes")).toHaveText("I'm still here");
    await shot(tabA, "idle-prompt");

    // 2. "I'm still here" in one tab closes the prompt in both and keeps the timer.
    await tabA.getByTestId("idle-check-yes").click();
    await expect(dialogA).toBeHidden({ timeout: 10_000 });
    await expect(dialogB).toBeHidden({ timeout: 10_000 });
    await expect(tabA.getByTestId("time-timer-bar")).toContainText(ref);

    // 3. Leave the next check unanswered: after the grace period the server stops the timer and the
    //    page offers Start again. (5 min to the next check + 5 min grace + up to 1 min for the sweep.)
    await expect(dialogA).toBeVisible({ timeout: 6 * 60_000 });
    await expect(tabA.getByTestId("idle-check-stopped")).toBeVisible({ timeout: 8 * 60_000 });
    await expect(tabA.getByTestId("idle-check-stopped")).toContainText("you didn't confirm");
    await expect(tabA.getByTestId("idle-check-resume")).toHaveText("Start again");
    await expect(dialogA).toBeHidden();
    await shot(tabA, "idle-resume");

    // 4. Start again starts a new timer on the same work item.
    await tabA.getByTestId("idle-check-resume").click();
    await expect(tabA.getByTestId("time-timer-bar")).toContainText(ref, { timeout: 15_000 });
    await expect(tabA.getByTestId("time-timer-bar-stop")).toBeVisible();
  } finally {
    await stopAnyTimer(admin.request).catch(() => undefined);
    if (previous !== null) {
      await admin.request.patch(api("time/settings/"), {
        data: { idle_check_min_minutes: previous.min, idle_check_max_minutes: previous.max },
      });
    }
    await deleteIssue(admin.request, project.id, issue.id);
    await admin.close();
  }
});
