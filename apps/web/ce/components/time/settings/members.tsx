/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — per-member tracking switch (with an effective date) and weekly capacity
// override. A blank capacity means the member follows the workspace work week.
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { setToast } from "@plane/blocks/toast";
import { Loader } from "@plane/blocks/skeleton";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTrackingMember, TTrackingMembersResponse } from "../reports/types";
import { HOLIDAY_CALENDARS_KEY, useHolidayCalendars } from "./holiday-data";
import { calendarSelectValue, defaultOptionLabel, holidayErrorMessage } from "./holidays-utils";
import { useTimeSettingsReadOnly } from "./read-only";
import type { THolidayCalendar } from "./types";
import { capacityToText, parseCapacityHours, settingsErrorMessage, trackingSince } from "./settings-utils";

export const MEMBERS_KEY = (slug: string) => ["time-members", slug] as const;
const ROLE_NAMES: Record<number, string> = { 20: "Admin", 15: "Member", 5: "Guest" };

type TRowProps = {
  workspaceSlug: string;
  member: TTrackingMember;
  defaultMinutes: number;
  today: string;
  // FORK: holidays/leave (PSR-122)
  calendars: THolidayCalendar[];
};

function MemberRow({ workspaceSlug, member, defaultMinutes, today, calendars }: TRowProps) {
  const [from, setFrom] = useState(today);
  const [capacityText, setCapacityText] = useState(() => capacityToText(member.weekly_capacity_minutes));
  const [busy, setBusy] = useState<"tracking" | "capacity" | "calendar" | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  const parsed = parseCapacityHours(capacityText);
  const savedText = capacityToText(member.weekly_capacity_minutes);
  const capacityDirty = capacityText.trim() !== savedText;
  const since = trackingSince(member.history, today);

  const refresh = () => mutate(MEMBERS_KEY(workspaceSlug));

  const toggleTracking = async () => {
    setBusy("tracking");
    try {
      await timeTrackingService.setMemberTracking(workspaceSlug, member.user_id, {
        is_tracked: !member.is_tracked,
        effective_from: from || undefined,
      });
      await refresh();
      setToast({
        type: "success",
        title: member.is_tracked ? "Tracking turned off" : "Tracking turned on",
        message: `${member.display_name}, from ${from}`,
      });
    } catch (err) {
      setToast({ type: "error", title: "Couldn't change tracking", message: settingsErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  const saveCapacity = async (minutes: number | null) => {
    setBusy("capacity");
    try {
      const res = await timeTrackingService.setMemberCapacity(workspaceSlug, member.user_id, minutes);
      setCapacityText(capacityToText(res.weekly_capacity_minutes));
      await refresh();
      setToast({
        type: "success",
        title: minutes === null ? "Back to the work week" : "Capacity saved",
        message: member.display_name,
      });
    } catch (err) {
      setToast({ type: "error", title: "Couldn't save capacity", message: settingsErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  // FORK: holidays/leave (PSR-122) — "" = use the workspace default list
  const saveCalendar = async (value: string) => {
    setBusy("calendar");
    try {
      await timeTrackingService.setMemberHolidayCalendar(workspaceSlug, member.user_id, value || null);
      // the list's member_count changed too
      await Promise.all([refresh(), mutate(HOLIDAY_CALENDARS_KEY(workspaceSlug))]);
      setToast({
        type: "success",
        title: "Holiday list saved",
        message: `${member.display_name}: ${calendars.find((c) => c.id === value)?.name ?? "workspace default"}`,
      });
    } catch (err) {
      setToast({ type: "error", title: "Couldn't change the holiday list", message: holidayErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <tr className="border-t border-subtle" data-testid="settings-member-row" data-user-id={member.user_id}>
      <td className="text-sm px-3 py-2 text-primary">
        {member.display_name}
        <span className="text-xs ml-2 text-tertiary">{ROLE_NAMES[member.role] ?? ""}</span>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span
            data-testid="member-tracking-state"
            className={cn(
              "text-xs rounded px-2 py-0.5",
              member.is_tracked ? "bg-success-subtle text-success-primary" : "bg-layer-1 text-tertiary"
            )}
          >
            {member.is_tracked ? "Tracked" : "Not tracked"}
          </span>
          {since && <span className="text-xs text-tertiary">since {since}</span>}
          {/* FORK: PSR-125 — read-only for a plain Admin: the state badge stays, the controls go */}
          {!readOnly && (
            <>
              <input
                type="date"
                aria-label={`Effective date for ${member.display_name}`}
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="text-xs rounded border border-subtle bg-transparent px-2 py-1"
              />
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={member.is_tracked ? "Stop tracking" : "Start tracking"}
                loading={busy === "tracking"}
                disabled={busy !== null || !from}
                onClick={toggleTracking}
                data-testid="member-tracking-toggle"
              />
            </>
          )}
        </div>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            inputMode="decimal"
            aria-label={`Weekly capacity in hours for ${member.display_name}`}
            data-testid="member-capacity-input"
            placeholder={`${(defaultMinutes / 60).toFixed(1)} (work week)`}
            value={capacityText}
            disabled={readOnly}
            onChange={(e) => setCapacityText(e.target.value)}
            className={cn(
              "text-sm w-32 rounded border bg-transparent px-2 py-1",
              parsed === "invalid" ? "border-danger-subtle" : "border-subtle"
            )}
          />
          <span className="text-xs text-tertiary">h / week</span>
          {!readOnly && (
            <Button
              variant="secondary"
              size="sm"
              stretch="auto"
              label="Save"
              loading={busy === "capacity"}
              disabled={busy !== null || !capacityDirty || parsed === "invalid"}
              onClick={() => parsed !== "invalid" && saveCapacity(parsed)}
              data-testid="member-capacity-save"
            />
          )}
          {!readOnly && member.weekly_capacity_minutes !== null && (
            <button
              type="button"
              className="text-xs text-accent-primary hover:underline"
              disabled={busy !== null}
              onClick={() => saveCapacity(null)}
              data-testid="member-capacity-reset"
            >
              Use work week
            </button>
          )}
          {parsed === "invalid" && <span className="text-xs text-danger-primary">0 to 168 hours</span>}
        </div>
      </td>
      <td className="px-3 py-2">
        <select
          aria-label={`Holiday list for ${member.display_name}`}
          data-testid="member-holiday-calendar"
          value={calendarSelectValue(member.holiday_calendar_id, calendars)}
          disabled={readOnly || busy !== null}
          onChange={(e) => saveCalendar(e.target.value)}
          className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
        >
          <option value="">{defaultOptionLabel(calendars)}</option>
          {calendars.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </td>
    </tr>
  );
}

/** One SWR entry for the tracked-people list, shared by the People table and the leave form. */
export function useTrackingMembers(workspaceSlug: string) {
  return useSWR<TTrackingMembersResponse>(
    MEMBERS_KEY(workspaceSlug),
    () => timeTrackingService.getTrackingMembers(workspaceSlug),
    { revalidateOnFocus: false }
  );
}

export function MembersSettings({ workspaceSlug }: { workspaceSlug: string }) {
  const { data, error, isLoading } = useTrackingMembers(workspaceSlug);
  // FORK: holidays/leave (PSR-122) — the "Holiday list" column
  const { data: calendars } = useHolidayCalendars(workspaceSlug);

  return (
    <section className="flex flex-col gap-3" data-testid="settings-members">
      <div>
        <h2 className="text-16 font-semibold text-primary">People</h2>
        <p className="text-xs text-tertiary">
          Tracking decides who is expected to log time. Weekly capacity overrides the work week for one person; leave it
          blank to follow the work week.
        </p>
      </div>
      {error && !data ? (
        <div role="alert" className="text-sm text-danger-primary">
          {settingsErrorMessage(error, "Couldn't load members.")}
        </div>
      ) : isLoading || !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="48px" />
          <Loader.Item height="48px" />
        </Loader>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-subtle bg-surface-1">
          <table className="w-full">
            <thead>
              <tr>
                <th className="text-xs px-3 py-2 text-left font-medium text-tertiary">Person</th>
                <th className="text-xs px-3 py-2 text-left font-medium text-tertiary">Time tracking</th>
                <th className="text-xs px-3 py-2 text-left font-medium text-tertiary">Weekly capacity</th>
                <th className="text-xs px-3 py-2 text-left font-medium text-tertiary">Holiday list</th>
              </tr>
            </thead>
            <tbody>
              {data.members.map((m) => (
                <MemberRow
                  key={m.user_id}
                  workspaceSlug={workspaceSlug}
                  member={m}
                  defaultMinutes={data.default_weekly_minutes}
                  today={data.date}
                  calendars={calendars ?? []}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
