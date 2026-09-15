/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — logs both seeded users in through the real sign-in endpoint
// (via the dev server's /auth proxy) and stores their session cookies for the
// specs. auth.spec.ts deliberately does NOT use these states.
import { chromium, type FullConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const E2E_EMAIL = process.env.E2E_EMAIL ?? "qa-visual@plane.test";
const E2E_OUTSIDER_EMAIL = process.env.E2E_OUTSIDER_EMAIL ?? "uat-outsider@example.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "QaTest#2026";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

async function loginAndStore(config: FullConfig, email: string, outPath: string) {
  const { baseURL } = config.projects[0].use;
  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL });

  // The sign-in view enforces CSRF and reads form fields (not JSON). A
  // successful login answers 302 WITHOUT an error_code in the location; do
  // not follow the redirect (it points at the real web app, possibly another
  // local port).
  const csrfRes = await context.request.get("/auth/get-csrf-token/");
  const { csrf_token: csrfToken } = (await csrfRes.json()) as { csrf_token: string };
  const response = await context.request.post("/auth/sign-in/", {
    form: { email, password: E2E_PASSWORD },
    headers: { "X-CSRFToken": csrfToken, Referer: String(baseURL) },
    maxRedirects: 0,
  });
  const location = response.headers()["location"] ?? "";
  const ok = response.status() === 302 && !location.includes("error_code");
  if (!ok) {
    const body = (await response.text()).slice(0, 200);
    await browser.close();
    throw new Error(
      `E2E setup: sign-in failed for ${email} (${response.status()} -> ${location.slice(0, 120)}). ` +
        `Did you run scripts/e2e-seed.sh against a running local stack? Body: ${body}`
    );
  }
  mkdirSync(here(".auth"), { recursive: true });
  await context.storageState({ path: outPath });
  await browser.close();
}

export default async function globalSetup(config: FullConfig) {
  await loginAndStore(config, E2E_EMAIL, here(".auth/admin.json"));
  await loginAndStore(config, E2E_OUTSIDER_EMAIL, here(".auth/outsider.json"));
}
