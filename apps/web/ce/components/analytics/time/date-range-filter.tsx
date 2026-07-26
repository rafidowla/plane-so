/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { Calendar } from "lucide-react";
import { endOfMonth, endOfWeek, startOfMonth, startOfWeek, subDays, subMonths, subWeeks } from "date-fns";
import { CustomSearchSelect } from "@plane/ui";
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

const PRESET_OPTIONS: { value: TDateRangePreset; label: string }[] = [
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

/** date-fns weekStartsOn: 0=Sunday..6=Saturday — matches EStartOfTheWeek. */
export function getPresetRange(
  preset: Exclude<TDateRangePreset, "custom">,
  weekStartsOn: 0 | 1 | 2 | 3 | 4 | 5 | 6 = 1
): { startDate: string; endDate: string } {
  const now = new Date();
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

export function DateRangeFilter({ preset, onChange }: Props) {
  const options = PRESET_OPTIONS.map((option) => ({
    value: option.value,
    query: option.label,
    content: <span className="flex-grow truncate">{option.label}</span>,
  }));

  return (
    <CustomSearchSelect
      value={[preset]}
      onChange={onChange}
      options={options}
      label={
        <div className="flex items-center gap-2 p-1">
          <Calendar className="h-4 w-4" />
          {PRESET_OPTIONS.find((opt) => opt.value === preset)?.label ?? "Custom range"}
        </div>
      }
    />
  );
}
