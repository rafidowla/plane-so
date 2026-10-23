/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 (S3b): a member asks for missing time, a workspace admin approves it and
// the entry appears; rejecting needs a note; the "Change pending" badge; members never see Approvals.
// Sessions: admin (qa-visual, workspace admin), member (qa-member, plain member of the E2E project).
import { expect, test, type APIRequestContext, type Browser, type BrowserContext } from "@playwright/test";
import { E2E_WS, createIssue, deleteIssue, getE2EProject, openWorkItem } from "./helpers";

const SHOT_DIR = process.env.E2E_SHOT_DIR;
const api = (path: string) => `/api/workspaces/${E2E_WS}/${path}`;

async function newSession(browser: Browser, who: "admin" | "member"): Promise<BrowserContext> {
  return browser.newContext({ storageState: `e2e/.auth/${who}.json` });
}

async function shot(page: import("@playwright/test").Page, name: string) {
  if (SHOT_DIR) await page.screenshot({ path: `${SHOT_DIR}/${name}.png` });
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A one-hour slot on a past day; random so reruns and leftovers never overlap. */
function randomPastSlot() {
  const daysBack = 3 + Math.floor(Math.random() * 300);
  const day = new Date(Date.now() - daysBack * 86_400_000).toISOString().slice(0, 10);
  const hour = Math.floor(Math.random() * 10); // 00:00-10:00 local, so the UTC-based second request never clashes
  return { day, start: `${day}T${pad(hour)}:00`, end: `${day}T${pad(hour + 1)}:00` };
}

/** A half-hour request at 23:00 UTC two days before `slot.day` (far from the slot in any time zone). */
async function submitRequest(request: APIRequestContext, issueId: string, slot: { day: string }, hour: number) {
  const earlier = new Date(new Date(`${slot.day}T00:00:00Z`).getTime() - 2 * 86_400_000).toISOString().slice(0, 10);
  slot = { day: earlier };
  const res = await request.post(api("time/requests/"), {
    data: {
      type: "missing_time",
      issue_id: issueId,
      started_at: `${slot.day}T${pad(hour)}:00:00Z`,
      ended_at: `${slot.day}T${pad(hour)}:30:00Z`,
      reason: "E2E: forgot to track",
    },
  });
  expect(res.ok(), `request failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as { id: string };
}

test("member requests missing time, admin approves it, the entry appears; reject needs a note", async ({ browser }) => {
  const admin = await newSession(browser, "admin");
  const member = await newSession(browser, "member");
  const adminPage = await admin.newPage();
  const memberPage = await member.newPage();
  const project = await getE2EProject(admin.request);
  const issueName = `E2E approvals ${Date.now()}`;
  let issueId = "";

  try {
    const issue = await createIssue(admin.request, project.id, issueName);
    issueId = issue.id;
    // The picker lists "my work items": assign the member.
    const me = await (await member.request.get("/api/users/me/")).json();
    const assign = await admin.request.patch(`/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}/`, {
      data: { assignee_ids: [me.id] },
    });
    expect(assign.ok()).toBeTruthy();

    // ---- member: Request missing time ----
    await memberPage.goto(`/${E2E_WS}/time/requests/`);
    await expect(memberPage.getByTestId("my-requests-status")).toBeVisible({ timeout: 30_000 });
    // Members never get the reviewer tab.
    await expect(memberPage.getByRole("tab", { name: /Approvals/ })).toHaveCount(0);
    await memberPage.getByTestId("request-missing-time").click();
    const modal = memberPage.getByTestId("request-modal");
    await expect(modal).toBeVisible();
    await modal.getByTestId("request-issue-input").fill(issueName);
    await modal.getByTestId("request-issue-option").first().click();

    // An empty reason is refused before anything is sent.
    const slot = randomPastSlot();
    await modal.getByTestId("request-start").fill(slot.start);
    await modal.getByTestId("request-end").fill(slot.end);
    await modal.getByTestId("request-submit").click();
    await expect(modal.getByTestId("request-error")).toBeVisible();

    await modal.getByTestId("request-reason").fill("E2E: forgot to start the timer");
    await shot(memberPage, "approvals-1-request-modal");
    await modal.getByTestId("request-submit").click();
    await expect(modal).toBeHidden();

    const mine = memberPage.getByTestId("request-row").filter({ hasText: issueName });
    await expect(mine).toHaveCount(1);
    await expect(mine.getByTestId("request-status")).toHaveText(/Pending/);
    await shot(memberPage, "approvals-2-my-requests");

    // ---- admin: Approve ----
    await adminPage.goto(`/${E2E_WS}/time/approvals/`);
    await expect(adminPage.getByTestId("approvals")).toBeVisible({ timeout: 30_000 });
    await expect(adminPage.getByRole("tab", { name: /Approvals \(\d+\)/ })).toBeVisible();
    const pending = adminPage.getByTestId("request-row").filter({ hasText: issueName });
    await expect(pending).toHaveCount(1);
    await shot(adminPage, "approvals-3-pending");
    await pending.getByTestId("request-approve").click();
    const review = adminPage.getByTestId("review-dialog");
    await review.getByTestId("review-note").fill("Looks right");
    await review.getByTestId("review-submit").click();
    await expect(review).toBeHidden();
    await expect(pending).toHaveCount(0);

    await adminPage.getByTestId("approvals-view-reviewed").click();
    const reviewed = adminPage.getByTestId("request-row").filter({ hasText: issueName });
    await expect(reviewed.getByTestId("request-status")).toHaveText(/Approved/);
    await shot(adminPage, "approvals-4-reviewed");

    // ---- the entry now exists ----
    const worklogs = await (
      await member.request.get(`/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issue.id}/worklogs/`)
    ).json();
    const list = (Array.isArray(worklogs) ? worklogs : worklogs.results) as { id: string; duration: number }[];
    expect(list).toHaveLength(1);
    expect(list[0].duration).toBe(60);
    await openWorkItem(memberPage, project.id, issue.id);
    await expect(memberPage.getByText("1h logged")).toBeVisible();

    // ---- Change pending badge: a change request on that entry ----
    const change = await member.request.post(api("time/requests/"), {
      data: { type: "deletion", worklog_id: list[0].id, reason: "E2E: logged by mistake" },
    });
    expect(change.ok(), `deletion request failed: ${change.status()} ${await change.text()}`).toBeTruthy();
    await memberPage.reload();
    await expect(memberPage.getByTestId("change-pending-badge")).toBeVisible({ timeout: 30_000 });
    await shot(memberPage, "approvals-5-change-pending");

    // ---- reject needs a note ----
    const second = await submitRequest(member.request, issue.id, slot, 23);
    await adminPage.goto(`/${E2E_WS}/time/approvals/`);
    const rows = adminPage.locator(`[data-testid="request-row"][data-request-id="${second.id}"]`);
    await expect(rows).toHaveCount(1, { timeout: 30_000 });
    await rows.getByTestId("request-reject").click();
    const rejectDialog = adminPage.getByTestId("review-dialog");
    await rejectDialog.getByTestId("review-submit").click();
    await expect(rejectDialog.getByTestId("review-error")).toBeVisible();
    await expect(rejectDialog).toBeVisible();
    await rejectDialog.getByTestId("review-note").fill("Not on this ticket");
    await shot(adminPage, "approvals-6-reject");
    await rejectDialog.getByTestId("review-submit").click();
    await expect(rejectDialog).toBeHidden();
    await expect(rows).toHaveCount(0);

    // The member sees the decision and the reason.
    await memberPage.goto(`/${E2E_WS}/time/requests/`);
    const decided = memberPage.locator(`[data-testid="request-row"][data-request-id="${second.id}"]`);
    await expect(decided.getByTestId("request-status")).toHaveText(/Rejected/);
    await expect(decided.getByTestId("request-decision")).toContainText("Not on this ticket");
  } finally {
    // Leave no pending requests or entries behind: withdraw, then remove the entry and the work item.
    const open = await member.request.get(api("time/requests/?scope=mine&status=pending&limit=200"));
    if (open.ok()) {
      for (const r of ((await open.json()) as { results: { id: string; issue_id: string }[] }).results) {
        // oxlint-disable-next-line no-await-in-loop -- sequential cleanup
        if (r.issue_id === issueId) await member.request.post(api(`time/requests/${r.id}/withdraw/`), { data: {} });
      }
    }
    if (issueId) {
      const wl = await admin.request.get(
        `/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issueId}/worklogs/`
      );
      if (wl.ok()) {
        const body = await wl.json();
        for (const w of (Array.isArray(body) ? body : (body.results ?? [])) as { id: string }[]) {
          // oxlint-disable-next-line no-await-in-loop -- sequential cleanup
          await admin.request.delete(
            `/api/workspaces/${E2E_WS}/projects/${project.id}/issues/${issueId}/worklogs/${w.id}/`
          );
        }
      }
      await deleteIssue(admin.request, project.id, issueId);
    }
    await admin.close();
    await member.close();
  }
});
