/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { Calendar } from "lucide-react";
import { endOfMonth, endOfWeek, startOfMonth, startOfWeek, subDays, subMonths, subWeeks } from "date-fns";
import { Select } from "@plane/blocks/select";
import { renderFormattedPayloadDate } from "@plane/utils";

export type TDateRangePreset =
  | "today"
  | "yesterday"
  | "this_week"
  | "last_week"
  | "this_month"
  | "last_month"
  | "last_7"
  | "last_15"
  | "last_30"
  | "custom";

type TPresetOption = { value: TDateRangePreset; label: string };

const PRESET_OPTIONS: TPresetOption[] = [
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "this_week", label: "This week" },
  { value: "last_week", label: "Last week" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "last_7", label: "Last 7 days" },
  { value: "last_15", label: "Last 15 days" },
  { value: "last_30", label: "Last 30 days" },
  { value: "custom", label: "Custom range" },
];

/**
 * Time-tracking weeks always run Monday to Sunday (spec v1.1), the same as the timesheet and the
 * server's weekly buckets. Deliberately NOT the user's profile "start of week" (PSR-127): that
 * defaults to Sunday and made Dashboard/Reports disagree with My timesheet.
 */
export const TIME_WEEK_STARTS_ON = 1 as const;

export function getPresetRange(
  preset: Exclude<TDateRangePreset, "custom">,
  now: Date = new Date()
): { startDate: string; endDate: string } {
  const weekStartsOn = TIME_WEEK_STARTS_ON;
  const fmt = (d: Date) => renderFormattedPayloadDate(d) ?? "";

  switch (preset) {
    case "today":
      return { startDate: fmt(now), endDate: fmt(now) };
    case "yesterday": {
      const y = subDays(now, 1);
      return { startDate: fmt(y), endDate: fmt(y) };
    }
    case "this_week":
      return { startDate: fmt(startOfWeek(now, { weekStartsOn })), endDate: fmt(endOfWeek(now, { weekStartsOn })) };
    case "last_week": {
      const lastWeek = subWeeks(now, 1);
      return {
        startDate: fmt(startOfWeek(lastWeek, { weekStartsOn })),
        endDate: fmt(endOfWeek(lastWeek, { weekStartsOn })),
      };
    }
    case "this_month":
      return { startDate: fmt(startOfMonth(now)), endDate: fmt(endOfMonth(now)) };
    case "last_month": {
      const lastMonth = subMonths(now, 1);
      return { startDate: fmt(startOfMonth(lastMonth)), endDate: fmt(endOfMonth(lastMonth)) };
    }
    case "last_7":
      return { startDate: fmt(subDays(now, 6)), endDate: fmt(now) };
    case "last_15":
      return { startDate: fmt(subDays(now, 14)), endDate: fmt(now) };
    case "last_30":
      return { startDate: fmt(subDays(now, 29)), endDate: fmt(now) };
  }
}

type Props = {
  preset: TDateRangePreset;
  onChange: (preset: TDateRangePreset) => void;
};

const isPreset = (value: string): value is TDateRangePreset => PRESET_OPTIONS.some((option) => option.value === value);

export function DateRangeFilter({ preset, onChange }: Props) {
  const selectedOption = PRESET_OPTIONS.find((option) => option.value === preset) ?? null;

  return (
    <Select<TPresetOption>
      getValues={() => PRESET_OPTIONS}
      value={selectedOption}
      onChange={(value) => {
        if (isPreset(value)) onChange(value);
      }}
      getOptionValue={(option) => option.value}
      getOptionLabel={(option) => option.label}
      pinSelected={false}
    >
      <Select.Trigger<TPresetOption> variant="select-md" prependIcon={<Calendar aria-hidden="true" />}>
        {(selected) => <span className="truncate">{selected[0]?.label ?? "Custom range"}</span>}
      </Select.Trigger>
    </Select>
  );
}
