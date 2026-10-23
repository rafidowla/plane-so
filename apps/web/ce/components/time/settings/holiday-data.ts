/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — SWR keys and hooks for the holiday lists, holidays and leave.
import useSWR from "swr";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { THoliday, THolidayCalendar, TLeave } from "./types";

export const HOLIDAY_CALENDARS_KEY = (slug: string) => ["time-holiday-calendars", slug] as const;
export const HOLIDAYS_KEY = (slug: string, calendarId: string, year: number) =>
  ["time-holidays", slug, calendarId, year] as const;
export const LEAVE_KEY = (slug: string) => ["time-leave", slug] as const;

export function useHolidayCalendars(workspaceSlug: string) {
  return useSWR<THolidayCalendar[]>(
    HOLIDAY_CALENDARS_KEY(workspaceSlug),
    () => timeTrackingService.getHolidayCalendars(workspaceSlug),
    { revalidateOnFocus: false }
  );
}

export function useHolidays(workspaceSlug: string, calendarId: string | undefined, year: number) {
  return useSWR<THoliday[]>(
    calendarId ? HOLIDAYS_KEY(workspaceSlug, calendarId, year) : null,
    calendarId ? () => timeTrackingService.getHolidays(workspaceSlug, calendarId, year) : null,
    { revalidateOnFocus: false }
  );
}

export function useLeave(workspaceSlug: string) {
  return useSWR<TLeave[]>(LEAVE_KEY(workspaceSlug), () => timeTrackingService.getLeave(workspaceSlug), {
    revalidateOnFocus: false,
  });
}
