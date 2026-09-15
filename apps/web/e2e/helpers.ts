/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — shared API helpers. All calls go through the dev server's
// /api proxy (same origin as the UI), so cookies apply to both.
import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";

export const E2E_WS = "e2e";
export const E2E_PROJECT_IDENTIFIER = "E2E";

export interface E2EProject {
  id: string;
}

export interface E2EIssue {
  id: string;
  sequence_id: number;
}

/** Taller than the comment collapse threshold (~320px) with plenty of margin. */
export const LONG_COMMENT_HTML = `<p>${Array.from({ length: 6 }, (_, p) =>
  Array.from(
    { length: 6 },
    (__unused, s) =>
      `Paragraph ${p + 1} sentence ${s + 1}: this long comment exists to verify that the activity feed collapses tall comments behind a Show more toggle so the timeline stays scannable.`
  ).join(" ")
).join("</p><p>")}</p>`;

function unwrap<T>(data: T | { results: T }): T {
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object" && "results" in data) {
    return (data as { results: T }).results;
  }
  return data;
}

export async function getE2EProject(request: APIRequestContext): Promise<E2EProject> {
  const res = await request.get(`/api/workspaces/${E2E_WS}/projects/`);
  expect(res.ok(), `projects list failed: ${res.status()}`).toBeTruthy();
  const projects = unwrap<{ id: string; identifier: string }[]>(await res.json());
  const project = projects.find((p) => p.identifier === E2E_PROJECT_IDENTIFIER);
  if (!project) {
    throw new Error(
      `E2E project ${E2E_PROJECT_IDENTIFIER} not found in workspace ${E2E_WS}. Run scripts/e2e-seed.sh first.`
    );
  }
  return { id: project.id };
}

export async function createIssue(request: APIRequestContext, projectId: string, name: string): Promise<E2EIssue> {
  const res = await request.post(`/api/workspaces/${E2E_WS}/projects/${projectId}/issues/`, {
    data: { name },
  });
  expect(res.ok(), `issue create failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  const issue = (await res.json()) as E2EIssue;
  return issue;
}

export async function deleteIssue(request: APIRequestContext, projectId: string, issueId: string) {
  await request.delete(`/api/workspaces/${E2E_WS}/projects/${projectId}/issues/${issueId}/`);
}

export async function postComment(request: APIRequestContext, projectId: string, issueId: string, commentHtml: string) {
  const res = await request.post(`/api/workspaces/${E2E_WS}/projects/${projectId}/issues/${issueId}/comments/`, {
    data: { comment_html: commentHtml },
  });
  expect(res.ok(), `comment create failed: ${res.status()}`).toBeTruthy();
  return res.json() as Promise<{ id: string }>;
}

export async function createWorklog(
  request: APIRequestContext,
  projectId: string,
  issueId: string,
  durationMinutes: number
) {
  const res = await request.post(`/api/workspaces/${E2E_WS}/projects/${projectId}/issues/${issueId}/worklogs/`, {
    data: {
      duration: durationMinutes,
      logged_date: new Date().toISOString().slice(0, 10),
      is_billable: false,
    },
  });
  expect(res.ok(), `worklog create failed: ${res.status()} ${await res.text()}`).toBeTruthy();
}

/** Stop any running timer for the signed-in user on this issue (best effort). */
export async function stopTimer(request: APIRequestContext, projectId: string, issueId: string) {
  await request.delete(`/api/workspaces/${E2E_WS}/projects/${projectId}/issues/${issueId}/worklog-timer/`);
}

/** Open a work item's detail view by UUID and wait for the detail UI. */
export async function openWorkItem(page: Page, projectId: string, issueId: string) {
  await page.goto(`/${E2E_WS}/projects/${projectId}/issues/${issueId}`);
  // Wait for the sidebar's Time tracking property (always present on the E2E
  // project) so the work item is genuinely loaded before asserting.
  await expect(page.getByText("Time tracking", { exact: true })).toBeVisible({ timeout: 30_000 });
}

/** Set a single-select OPTION property value on an issue (e.g. task-type=Bug). */
export async function setOptionValue(
  request: APIRequestContext,
  projectId: string,
  issueId: string,
  propertyName: string,
  optionName: string
) {
  const res = await request.get(`/api/workspaces/${E2E_WS}/projects/${projectId}/work-item-properties/`);
  expect(res.ok(), `properties list failed: ${res.status()}`).toBeTruthy();
  const properties = unwrap<{ id: string; name: string; options?: { id: string; name: string }[] }[]>(await res.json());
  const property = properties.find((p) => p.name === propertyName);
  if (!property) throw new Error(`property ${propertyName} not found — run scripts/e2e-seed.sh`);
  const option = (property.options ?? []).find((o) => o.name === optionName);
  if (!option) throw new Error(`option ${optionName} not found on ${propertyName}`);
  const setRes = await request.post(
    `/api/workspaces/${E2E_WS}/projects/${projectId}/work-items/${issueId}/work-item-properties/${property.id}/values/`,
    { data: { values: [option.id] } }
  );
  expect(setRes.ok(), `value set failed: ${setRes.status()} ${await setRes.text()}`).toBeTruthy();
}
