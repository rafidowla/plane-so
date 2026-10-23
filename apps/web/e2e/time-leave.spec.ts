/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: e2e — time tracking v2 holidays & leave (PSR-122). As admin: create a holiday list, add a holiday on
// a working day of this week, assign the admin to the list, add a half-day leave for the admin on another
// working day, then open My timesheet and check both day markers show and the week's expected dropped.
import { expect, test, type APIRequestContext } from "@playwright/test";
import { E2E_WS } from "./helpers";

const api = (path: string) => `/api/workspaces/${E2E_WS}/${path}`;

type TDay = { date: string; is_weekend: boolean; tracked: boolean; expected_minutes: number; off: unknown };
type TWeek = { days: TDay[]; totals: { expected: number } };

// same wording as formatLoggedMinutes (5h 30m / 8h / 45m)
const fmt = (total: number) => {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}h ${m}m`;
  return h ? `${h}h` : `${m}m`;
};

async function getWeek(request: APIRequestContext): Promise<TWeek> {
  const res = await request.get(api("time/timesheet/"));
  expect(res.ok(), `timesheet failed: ${res.status()}`).toBeTruthy();
  return (await res.json()) as TWeek;
}

test.describe("Holidays and leave", () => {
  test.use({ storageState: "e2e/.auth/admin.json" });

  test("a holiday and a half-day leave lower this week's expected hours", async ({ page, request }) => {
    const me = (await (await request.get("/api/users/me/")).json()) as { id: string };
    const before = await getWeek(request);
    // two working days of this week on which the admin is expected to log time and has no day off yet
    const workdays = before.days.filter((d) => !d.is_weekend && d.tracked && d.expected_minutes > 0 && !d.off);
    test.skip(workdays.length < 2, "needs two tracked working days in the current week");
    const [holiday, leave] = workdays;

    const listName = `E2E list ${Date.now()}`;
    let calendarId: string | null = null;

    try {
      // 1. A holiday list with one holiday on a working day.
      await page.goto(`/${E2E_WS}/time/settings/`);
      await expect(page.getByTestId("settings-holidays")).toBeVisible({ timeout: 30_000 });
      await page.getByTestId("holiday-calendar-new-name").fill(listName);
      await page.getByTestId("holiday-calendar-new-submit").click();
      const calendarRow = page.getByTestId("holiday-calendar-row").filter({ hasText: listName });
      await expect(calendarRow).toBeVisible();
      calendarId = await calendarRow.getAttribute("data-calendar-id");

      // the new list is selected: its holidays panel is shown
      await expect(page.getByTestId("holiday-panel")).toHaveAttribute("data-calendar-id", calendarId ?? "");
      await page.getByTestId("holiday-new-date").fill(holiday.date);
      await page.getByTestId("holiday-new-name").fill("E2E holiday");
      await page.getByTestId("holiday-new-submit").click();
      await expect(page.locator(`[data-testid="holiday-row"][data-date="${holiday.date}"]`)).toContainText(
        "E2E holiday"
      );

      // 2. Put the admin on that list (so the default list, if any, does not decide).
      const memberRow = page.locator(`[data-testid="settings-member-row"][data-user-id="${me.id}"]`);
      await memberRow.getByTestId("member-holiday-calendar").selectOption({ label: listName });
      await expect(memberRow.getByTestId("member-holiday-calendar")).toHaveValue(calendarId ?? "");

      // 3. A half-day leave for the admin on another working day.
      await page.getByTestId("leave-new-person").selectOption(me.id);
      await page.getByTestId("leave-new-start").fill(leave.date);
      await page.getByTestId("leave-new-end").fill(leave.date);
      await page.getByTestId("leave-new-portion").selectOption("half");
      await page.getByTestId("leave-new-note").fill("E2E half day");
      await page.getByTestId("leave-new-submit").click();
      const leaveRow = page.locator(`[data-testid="leave-row"][data-user-id="${me.id}"]`).filter({
        hasText: "E2E half day",
      });
      await expect(leaveRow).toBeVisible();
      await expect(leaveRow).toContainText("Half day");

      // 4. My timesheet: both markers, and expected dropped by a full day plus a half day.
      await page.goto(`/${E2E_WS}/time/timesheet/`);
      await expect(page.getByTestId("timesheet-tab")).toBeVisible({ timeout: 30_000 });
      const holidayMarker = page.locator(`[data-testid="timesheet-day-off"][data-date="${holiday.date}"]`);
      const leaveMarker = page.locator(`[data-testid="timesheet-day-off"][data-date="${leave.date}"]`);
      await expect(holidayMarker).toHaveAttribute("data-kind", "holiday");
      await expect(holidayMarker).toHaveAttribute("data-portion", "full");
      await expect(holidayMarker).toHaveAttribute("title", /E2E holiday/);
      await expect(leaveMarker).toHaveAttribute("data-kind", "leave");
      await expect(leaveMarker).toHaveAttribute("data-portion", "half");

      const expectedDrop = holiday.expected_minutes + leave.expected_minutes / 2;
      const after = await getWeek(request);
      expect(after.totals.expected).toBe(before.totals.expected - expectedDrop);
      await expect(page.getByTestId("timesheet-week-expected")).toContainText(fmt(after.totals.expected));
    } finally {
      // best-effort cleanup: leave rows, the assignment, then the list
      const leaves = await request.get(api(`time/leave/?user_id=${me.id}`));
      if (leaves.ok()) {
        const mine = ((await leaves.json()) as { id: string; note: string }[]).filter(
          (row) => row.note === "E2E half day"
        );
        await Promise.all(mine.map((row) => request.delete(api(`time/leave/${row.id}/`))));
      }
      await request.put(api(`time/members/${me.id}/holiday-calendar/`), { data: { calendar_id: null } });
      if (calendarId) await request.delete(api(`time/holiday-calendars/${calendarId}/`));
    }
  });
});
